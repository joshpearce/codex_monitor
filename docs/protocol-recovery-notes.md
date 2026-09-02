# Monitoring and Recovering Codex App-Server Sessions

This document records the practical protocol and operational knowledge needed to
monitor a long-running Codex thread and recover its existing Goal when the
session unloads or stops making progress.

The interface described here is experimental and was verified against Codex
CLI/app-server `0.152.1` on macOS. Generate schemas from the installed Codex
version before depending on field names or behavior:

```sh
schema_dir="$(mktemp -d)"
codex app-server generate-json-schema --experimental --out "$schema_dir"
```

Do not treat this runbook as a stable public API contract. Pin or record the
Codex version used by the monitor and fail closed on unfamiliar responses.

## Architecture

The managed app-server listens on a Unix-domain socket:

```text
~/.codex/app-server-control/app-server-control.sock
```

On the tested host this is a WebSocket server over the Unix socket. It is not a
raw JSON-lines socket. Connect with a WebSocket implementation that supports a
pre-connected Unix socket, such as `websockets.unix_connect()` in Python.

A TUI started with the following command is a client of this same app-server:

```sh
codex --remote unix://
```

Thread execution is server-owned. A monitor opens another WebSocket connection
to the same app-server; it does not need to proxy through the TUI.

Useful topology checks are:

```sh
lsof -n -U | grep '/Users/example/.codex/app-server-control/app-server-control.sock'
pgrep -fl codex
```

In `lsof`, the app-server PID owns the named listening socket and accepted
server-side descriptors. Client descriptors usually appear as arrows to the
accepted socket's kernel address. Match those addresses to establish which
Codex process is connected. Do not infer topology from process names alone.

## Transport details

The Python package `websockets` 15 supports Unix sockets:

```python
import websockets

ws = await websockets.unix_connect(
    "/Users/example/.codex/app-server-control/app-server-control.sock",
    uri="ws://localhost/",
    compression=None,
    open_timeout=10,
    close_timeout=3,
    max_size=32 * 1024 * 1024,
)
```

`uri` supplies the WebSocket HTTP Host and request target; the actual connection
uses the Unix socket in `path`.

On the tested installation, the first connection attempt with WebSocket
compression negotiation enabled ended with `InvalidMessage: did not receive a
valid HTTP response`. Retrying with `compression=None` produced a normal HTTP
`101 Switching Protocols` response. Capture the Upgrade response and exception
when connecting fails. Do not fall back to writing raw JSON to the socket.

Messages are JSON text WebSocket frames. Requests have `method`, `id`, and
usually `params`. Notifications omit `id`. This protocol did not require a
`"jsonrpc":"2.0"` member in the tested build.

## Required initialization

`initialize` must be the first protocol request:

```json
{
  "method": "initialize",
  "id": 1,
  "params": {
    "clientInfo": {
      "name": "codex-monitor",
      "version": "0.1.0"
    },
    "capabilities": {
      "experimentalApi": true,
      "requestAttestation": false
    }
  }
}
```

Wait for the response whose `id` is `1`, then send:

```json
{"method":"initialized"}
```

The response includes the server's `userAgent`, `codexHome`, `platformFamily`,
and `platformOs`. Record these values for diagnostics.

Responses and asynchronous notifications can be interleaved. A client must
continue reading until it receives the matching response ID while separately
processing or buffering notifications. Never assume the next received frame is
the response to the last request.

## Important state distinctions

Three pieces of state answer different questions:

1. `thread/loaded/list` says which threads are loaded in this app-server.
2. `thread/read` reports the thread's current runtime status and metadata.
3. `thread/goal/get` reports the durable Goal state associated with the thread.

These values must not be collapsed into one boolean.

Common thread statuses observed in practice are:

- `notLoaded`: persisted metadata exists, but this app-server has not loaded it.
- `idle`: loaded and able to accept work, with no active turn at that instant.
- `active`: a turn is running. `activeFlags` may provide additional detail.

`canAcceptDirectInput:true` means the loaded thread can accept direct input; it
does not by itself prove that a turn is executing.

Goal statuses in the tested schema are:

- `active`
- `paused`
- `blocked`
- `usageLimited`
- `budgetLimited`
- `complete`

An `active` Goal means the durable objective remains active. It does not, by
itself, prove that a turn is currently running. Confirm execution with thread
status and `turn/started` / completion notifications.

Likewise, `notLoaded` does not mean that a Goal is blocked or complete. It means
the runtime session must be loaded before it can continue.

## Discovering the correct thread

Prefer an exact thread ID obtained from the TUI or another trusted source. Do
not select a recovery target solely by Goal objective, preview, title, or cwd.
Older persisted threads can have nearly identical Goals.

List all currently loaded IDs:

```json
{"method":"thread/loaded/list","id":2,"params":{}}
```

To investigate candidates, query `thread/list` without `sourceKinds` first:

```json
{
  "method": "thread/list",
  "id": 3,
  "params": {
    "cwd": "/Users/example/code/example-project",
    "sortKey": "updated_at",
    "sortDirection": "desc",
    "limit": 20
  }
}
```

An app-server-backed TUI may be recorded with source `vscode` even when it is
running in a terminal. If an explicit filter is required, include every
interactive source relevant to the installed schema:

```json
"sourceKinds": ["cli", "vscode", "appServer"]
```

There is another discovery trap: the thread's recorded `cwd` may differ from
the repository containing its Goal document. In the verified incident, the
Goal referenced `/Users/example/code/example-project/...`, but the thread cwd was
`/Users/example`. A `thread/list` query filtered to the repository cwd therefore
did not return the live thread.

Once the exact ID is known, use it directly.

## Read-only health snapshot

Read metadata without hydrating turns:

```json
{
  "method": "thread/read",
  "id": 4,
  "params": {
    "threadId": "THREAD_ID",
    "includeTurns": false
  }
}
```

Read its Goal:

```json
{
  "method": "thread/goal/get",
  "id": 5,
  "params": {
    "threadId": "THREAD_ID"
  }
}
```

Before any recovery action, persist the entire Goal object returned by
`thread/goal/get`, including:

- `threadId`
- `objective`
- `status`
- `tokenBudget`
- `tokensUsed`
- `timeUsedSeconds`
- `createdAt`
- `updatedAt`

This is evidence for recovery and guards against silently replacing a Goal that
disappears.

## Conservative recovery sequence

Use the following sequence for one exact, user-approved thread ID:

1. Initialize the protocol.
2. Call `thread/read` with `includeTurns:false`.
3. Call `thread/goal/get` and save the complete returned Goal.
4. Call `thread/loaded/list`.
5. If and only if the exact ID is not loaded or status is `notLoaded`, call
   `thread/resume`.
6. Call `thread/goal/get` again.
7. If the Goal disappeared, stop and report the saved pre-resume Goal. Do not
   invent or recreate it automatically.
8. If the same Goal exists but has a non-active recoverable status, apply the
   operator's explicit policy before changing it.
9. Verify with fresh `thread/read`, `thread/goal/get`, and
   `thread/loaded/list` requests.

Minimal resume request:

```json
{
  "method": "thread/resume",
  "id": 6,
  "params": {
    "threadId": "THREAD_ID",
    "excludeTurns": true
  }
}
```

`excludeTurns:true` avoids loading the full history into the response. The
tested schema describes full-history hydration as deprecated for paginated
threads. Use paginated turn/item APIs only when history is actually needed.

Do not pass model, cwd, sandbox, permissions, approval policy, personality, or
service-tier overrides unless changing those settings is explicitly intended.
Minimal recovery should preserve the persisted thread configuration.

If policy explicitly permits reactivating the existing Goal, update only its
status:

```json
{
  "method": "thread/goal/set",
  "id": 7,
  "params": {
    "threadId": "THREAD_ID",
    "status": "active"
  }
}
```

Omitting `objective` and `tokenBudget` avoids replacing their saved values. Do
not call `thread/goal/set` if `thread/goal/get` returns no Goal after resume.

### Resume can start work automatically

For a thread whose persisted Goal was already `active`, `thread/resume`
preserved the Goal and automatically emitted a new `turn/started` notification.
No `turn/start` call was required. The observed sequence included:

1. `thread/status/changed` to `idle`
2. `thread/goal/updated` with the existing active Goal
3. `thread/status/changed` to `active`
4. `turn/started` with an `inProgress` turn

Therefore, never combine unconditional `thread/resume` with an unconditional
`turn/start`; that risks duplicate or conflicting work.

## What “stuck” should mean

Do not declare a session stuck merely because no assistant text appeared
recently. Long-running commands, tests, model reasoning, compaction, approvals,
or tool calls can legitimately be quiet.

A monitor should track:

- loaded membership
- thread status and status transitions
- Goal status and `updatedAt`
- turn ID, status, and start time
- protocol notifications
- approval or elicitation requests
- command/tool start and completion events
- errors, usage limits, and budget limits
- app-server process and socket availability

Use separate thresholds for:

- disconnected/unloaded: deterministic and usually safe to recover by resume
- blocked/paused: requires understanding or an explicit operator policy
- usage/budget limited: never bypass automatically
- active but quiet: investigate notifications and external processes before
  intervening
- active with a known failed turn: report the turn error and seek a targeted
  retry policy

An `updatedAt` timestamp alone is not a sufficient heartbeat. A subprocess may
still be working without updating thread metadata.

## Operations that should not be automatic

Unless an operator has explicitly authorized a narrowly defined policy, a
watchdog should not call:

- `thread/start`
- `turn/start`
- `turn/steer`
- `thread/goal/set`
- thread deletion, archive, rollback, revert, or fork methods
- daemon stop/restart

Never create a replacement thread or Goal merely because lookup failed. First
distinguish an incorrect ID, filter mismatch, unloaded thread, app-server
restart, schema mismatch, and actual missing state.

`blocked`, `paused`, `usageLimited`, and `budgetLimited` carry different intent.
Blindly converting all of them to `active` can defeat safety, cost, or
human-decision boundaries.

## Reference Python client

The following client implements initialization, response correlation, a
read-only snapshot, and an explicitly enabled recovery path. It requires an
exact thread ID. Recovery is disabled unless `--recover` is provided.

```python
#!/usr/bin/env python3
import argparse
import asyncio
import json
from dataclasses import dataclass, field

import websockets


@dataclass
class ProtocolClient:
    ws: object
    next_id: int = 1
    notifications: list[dict] = field(default_factory=list)

    async def call(self, method: str, params: dict) -> dict:
        request_id = self.next_id
        self.next_id += 1
        await self.ws.send(json.dumps({
            "method": method,
            "id": request_id,
            "params": params,
        }))
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=30)
            message = json.loads(raw)
            if message.get("id") == request_id:
                return message
            self.notifications.append(message)

    async def initialize(self) -> dict:
        response = await self.call("initialize", {
            "clientInfo": {"name": "codex-monitor", "version": "0.1.0"},
            "capabilities": {
                "experimentalApi": True,
                "requestAttestation": False,
            },
        })
        if "error" in response:
            raise RuntimeError(f"initialize failed: {response['error']}")
        await self.ws.send(json.dumps({"method": "initialized"}))
        return response


def result(response: dict) -> dict:
    if "error" in response:
        raise RuntimeError(json.dumps(response["error"], sort_keys=True))
    return response.get("result", {})


async def inspect_and_maybe_recover(socket_path: str, thread_id: str,
                                    recover: bool) -> dict:
    report = {"threadId": thread_id, "actions": []}
    async with websockets.unix_connect(
        socket_path,
        uri="ws://localhost/",
        compression=None,
        open_timeout=10,
        close_timeout=3,
        max_size=32 * 1024 * 1024,
    ) as ws:
        client = ProtocolClient(ws)
        report["initialize"] = await client.initialize()

        before_read = await client.call("thread/read", {
            "threadId": thread_id,
            "includeTurns": False,
        })
        before_goal = await client.call("thread/goal/get", {
            "threadId": thread_id,
        })
        before_loaded = await client.call("thread/loaded/list", {})

        report["before"] = {
            "thread": before_read,
            "goal": before_goal,
            "loaded": before_loaded,
        }
        # Persist report["before"]["goal"] durably here before recovery.

        thread = result(before_read).get("thread", {})
        goal = result(before_goal).get("goal")
        loaded = result(before_loaded).get("data", [])
        is_loaded = thread_id in loaded and thread.get("status", {}).get("type") != "notLoaded"

        if recover and not is_loaded:
            report["resume"] = await client.call("thread/resume", {
                "threadId": thread_id,
                "excludeTurns": True,
            })
            result(report["resume"])
            report["actions"].append("thread/resume")

            after_resume_goal = await client.call("thread/goal/get", {
                "threadId": thread_id,
            })
            report["goalAfterResume"] = after_resume_goal
            resumed_goal = result(after_resume_goal).get("goal")

            if goal is not None and resumed_goal is None:
                report["recoveryStopped"] = (
                    "Goal disappeared after resume; saved pre-resume Goal retained"
                )
            # Reactivation is deliberately not automatic in this example.

        report["final"] = {
            "thread": await client.call("thread/read", {
                "threadId": thread_id,
                "includeTurns": False,
            }),
            "goal": await client.call("thread/goal/get", {
                "threadId": thread_id,
            }),
            "loaded": await client.call("thread/loaded/list", {}),
        }
        report["notifications"] = client.notifications
        return report


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("thread_id")
    parser.add_argument(
        "--socket",
        default="/Users/example/.codex/app-server-control/app-server-control.sock",
    )
    parser.add_argument("--recover", action="store_true")
    return parser.parse_args()


async def main():
    args = parse_args()
    report = await inspect_and_maybe_recover(
        args.socket, args.thread_id, args.recover
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
```

Install the required library in an isolated environment if it is not already
available:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install 'websockets>=15,<16'
```

Read-only inspection:

```sh
.venv/bin/python monitor.py THREAD_ID
```

Explicit recovery of an unloaded exact thread:

```sh
.venv/bin/python monitor.py --recover THREAD_ID
```

## Monitoring loop design

A production monitor should keep one initialized WebSocket open and continuously
consume notifications. It should also take periodic snapshots because a
connection may miss events during reconnect.

Recommended state machine:

```text
CONNECT
  -> INITIALIZE
  -> SNAPSHOT exact thread + Goal + loaded IDs
  -> OBSERVE notifications
       -> unloaded/disconnected: reconnect, snapshot, optionally resume
       -> idle + active Goal: wait briefly; verify whether goal runner starts
       -> active: track turn and tools; do not interfere
       -> blocked/paused: report reason/context; apply explicit policy only
       -> usage/budget limited: alert; never auto-reactivate
       -> complete: record completion and stop recovery
  -> PERIODIC RECONCILIATION SNAPSHOT
```

Use bounded exponential backoff for transport reconnection. Keep protocol
reconnection separate from thread recovery: reconnecting the monitor should not
automatically resume every known thread.

Persist an append-only audit record containing:

- timestamp and Codex version
- exact thread ID
- request method and response ID
- before/after thread status
- before/after Goal status and objective hash or full secured object
- recovery action and reason
- relevant notifications and turn IDs
- errors and HTTP Upgrade diagnostics

Never log credentials, bearer tokens, private prompts, or full repository data
unless the audit destination is explicitly approved for them.

## Verification checklist

After recovery, verify all of the following independently:

- exact thread ID is present in `thread/loaded/list`
- `thread/read` status is `idle` or `active`, not `notLoaded`
- `thread/goal/get` returns the same Goal objective
- Goal status is the intended status
- a `turn/started` notification exists if execution should be underway
- a later snapshot reports thread `active` when a turn is running
- tokens/time/update timestamps advance only when expected
- no new thread or Goal was created

Immediate responses and notifications can race. For example, resume may return
an `idle` thread while queued notifications already describe its transition to
`active`. Take a fresh read-only snapshot after the notification burst before
declaring the final state.

## Incident learned from the verified recovery

For thread `THREAD_ID`:

- persisted metadata and the active Goal existed while the thread was unloaded
- `thread/resume` with `excludeTurns:true` loaded the exact existing thread
- the Goal survived with its original objective and `active` status
- no `thread/goal/set` call was necessary
- resume automatically started a new turn
- a subsequent snapshot showed the thread in `thread/loaded/list`, thread status
  `active`, and Goal status `active`

This is the preferred recovery shape: identify exactly, snapshot durably,
resume minimally, avoid redundant mutation, and verify from fresh state.
