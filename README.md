# Codex Goal Monitor

`codex-goal-monitor` is an intentionally aggressive, one-shot supervisor for trusted Codex projects.
It starts the user's Codex app-server daemon if necessary, reconnects to configured threads, reactivates
paused or blocked Goals, starts a continuation when a Goal is idle, and approves requests received
during the reconciliation window.

It runs every five minutes as a macOS LaunchAgent or Linux `systemd --user` timer and does not depend
on a permanent WebSocket connection.

> This program deliberately removes safety pauses for explicitly configured threads. Only list projects
> whose Goals and host environment you trust.

The protocol investigation is preserved in
[`docs/protocol-recovery-notes.md`](docs/protocol-recovery-notes.md).

## Requirements and installation

- Python 3.11 or newer
- A Codex CLI with `codex app-server daemon`
- App-server protocol compatible with Codex CLI 0.152.1

Install with `pipx`, or create a development environment:

```sh
pipx install /path/to/codex_monitor

python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
```

Copy and edit the configuration:

```sh
mkdir -p ~/.config/codex-monitor
cp config/projects.example.toml ~/.config/codex-monitor/projects.toml
```

Use exact Codex thread IDs. A project path is not a reliable identity because a thread's recorded working
directory can differ from the repository containing its Goal.

For each autonomous project, put this in `<project>/.codex/config.toml`:

```toml
approval_policy = "never"
approvals_reviewer = "auto_review"
```

The project must be trusted by Codex. Project-local settings are preferred; the monitor does not override
them unless `[defaults]` in `projects.toml` explicitly requests overrides.

## Usage

```sh
codex-goal-monitor reconcile
codex-goal-monitor inspect
codex-goal-monitor status
```

Install the checked-in service template for your platform with:

```sh
# macOS LaunchAgent
scripts/install-macos.sh ~/.config/codex-monitor/projects.toml

# Linux systemd user service and timer
scripts/install-systemd-user.sh ~/.config/codex-monitor/projects.toml
```

Both scripts resolve `codex-goal-monitor` from `PATH`. Set `CODEX_GOAL_MONITOR_BIN` to an absolute
executable path when it is installed somewhere not present in the installer shell's `PATH`. The macOS
installer writes `~/Library/LaunchAgents/com.codex-goal-monitor.plist`; the Linux installer writes into
`${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user`. Templates are under [`services`](services).
On Linux, run `loginctl enable-linger "$USER"` separately if the timer should operate while the user is
logged out.

Remove the installed user service without removing monitor configuration or state:

```sh
# macOS
scripts/uninstall-macos.sh

# Linux
scripts/uninstall-systemd-user.sh
```

## Reconciliation behavior

Each invocation:

1. Takes a non-blocking per-user lock; overlapping runs exit successfully.
2. Runs `codex app-server daemon start` and waits for its Unix socket.
3. Connects with WebSocket compression disabled and initializes the protocol.
4. Records compact before-state fields for each exact thread and Goal before changing it.
5. Resumes unloaded threads; resume may start an active Goal automatically.
6. Changes `paused`, `blocked`, `usageLimited`, or `budgetLimited` back to `active`.
7. Starts a continuation when the Goal is active but the thread is idle.
8. Accepts approval and user-input requests received during a short drain window.
9. Writes private state and an append-only audit log under `~/.local/state/codex-monitor/`.

A configured `goal_objective` is an identity check. A mismatch fails that project without modifying it.
Completed Goals and projects without a Goal are left alone. A 240-second continuation cooldown prevents
duplicate starts while permitting the next regular five-minute invocation to recover an idle Goal.

See [`config/projects.example.toml`](config/projects.example.toml) for all common settings.

## Handled conditions

The monitor acts only on explicitly configured thread IDs. The following table is the operator-facing
contract for what it currently notices and how it responds.

| Notice | Classification / audit value | Response | Important limitation |
| --- | --- | --- | --- |
| Codex app-server daemon is unavailable | Run-level startup condition | Runs `codex app-server daemon start`, then waits for the Unix socket | A startup failure ends the run and is recorded as `run-failed` |
| Another monitor invocation holds the lock | Overlapping run | Exits successfully without touching any thread | The skipped invocation does not wait for the first one |
| Configured thread is unloaded or `notLoaded` | Thread recovery | Calls `thread/resume` with configured approval overrides | Resume may itself start a Goal turn |
| Goal is `paused` or `blocked` | Usually `approval_question` | Changes the Goal to `active` and starts or steers a turn with the configured generic affirmative answer | This deliberately treats configured blocked Goals as authorized unless a more specific classifier matches |
| Goal is `usageLimited` or `budgetLimited` | Recoverable Goal state | Changes the Goal to `active`; starts a continuation if the thread is idle | It does not acquire quota or change an actual account limit, so repeated failures may remain unchanged |
| Goal is active and thread is idle | `idle_or_unknown` / `continuation` | Starts a turn asking Codex to resolve the previous stop and continue the full Goal | A cooldown suppresses duplicate continuation turns |
| Goal and thread are both active | `already-active` | Makes no turn change and observes state again on the next timer run | It does not determine whether an active turn is making useful progress |
| Goal is complete | `complete` | Leaves it untouched | Completed Goals are not reopened |
| Goal is missing or project monitoring is disabled | `disabled-or-no-goal` | Leaves the thread untouched | The monitor does not create Goals from configuration |
| Goal objective differs from configured `goal_objective` | Project error | Refuses to modify the thread | Exact objective matching is an identity guard |
| Assistant asks a natural-language authorization question | `approval_question` / `generic_approval` | Sends the configured generic affirmative answer in a new or active turn | Detection is keyword-based and can miss unfamiliar wording |
| Claude reports unauthenticated status, requests `claude /login`, or reports no host-side runner | `claude_sandbox_auth` / `claude_host_recovery` | Instructs Codex to use the already-authorized host Claude credentials and requests `danger-full-access` for the turn | A thread created without host execution cannot reliably be upgraded later; create such threads with host access initially |
| Codex requests command execution approval | Protocol auto-approval | Chooses `acceptForSession` when offered, otherwise `accept` | Applies only to configured thread IDs |
| Codex requests file-change approval | Protocol auto-approval | Returns `accept` | Applies only to configured thread IDs |
| Codex requests permissions | Protocol auto-approval | Grants the requested permissions for the session with strict auto-review disabled | The requested permission set is accepted as supplied by Codex |
| Codex requests structured user input | Protocol auto-response | Selects the first offered option, or the generic affirmative answer when there are no options | The first option is not semantically evaluated |
| MCP server requests elicitation | Protocol auto-response | Accepts with empty content | Cannot satisfy an elicitation that requires non-empty structured data |
| Legacy exec or patch approval is requested | Protocol auto-approval | Approves it, for the session where supported | Retained for protocol compatibility |
| Same blocker text recurs | Same `blockerFingerprint`; incremented `sameBlockerCount` | Records the recurrence, then applies the otherwise selected recovery | It currently reports repetition but does not change strategy or stop automatically |

> **Trust boundary:** these responses are intentionally aggressive. Command, file, permission, and user
> input requests are approved without human review for configured threads. Only configure Goals whose
> repository, instructions, external-service use, and host environment you are prepared to trust.

The Claude-specific failure signatures and the evidence behind that recovery are described in
[`docs/failure-cases.md`](docs/failure-cases.md). Unknown assistant messages fall back to the normal
continuation behavior; there is no general LLM-based blocker interpreter yet.

## Observability

Each reconciliation prints compact JSON to stdout. Linux records it in the user journal:

```sh
journalctl --user -u codex-goal-monitor.service --since today
```

The macOS LaunchAgent writes stdout and stderr to
`~/Library/Logs/codex-goal-monitor.log` and `~/Library/Logs/codex-goal-monitor.error.log`.

Both platforms also receive an append-only, mode-`0600` JSONL audit stream at
`~/.local/state/codex-monitor/audit.jsonl` by default. Every invocation has a UUID `runId` and emits
`run-started` plus `run-finished` or `run-failed`. Per-project records contain before/after Goal and
thread states, elapsed time, actions, recovery strategy, a redacted SHA-256 blocker fingerprint, and the
number of consecutive encounters with that blocker. The raw assistant blocker text is deliberately not
logged. This makes recurring ineffective recoveries visible without copying repository-derived session
messages into the service journal.

Useful outcomes include `recovery-submitted`, `already-active`, `continuation-cooldown`, `complete`, and
`error`. Compare `blockerFingerprint` and `sameBlockerCount` across runs to distinguish progress from a
five-minute restart loop.

## Opt-in integration harness

[`scripts/run-example-project-integration.sh`](scripts/run-example-project-integration.sh) exercises one real,
explicitly configured thread without installing a timer. It takes a read-only before snapshot, runs one
reconciliation, waits briefly, and takes an after snapshot. Its companion TOML pins both the thread ID and
the complete Goal objective so a stale or mistaken target fails before mutation.

The reusable black-box suite lives under [`integration/canned_approval`](integration/canned_approval).
It copies a tiny standard-library JSON-formatter project to a temporary directory, creates a new Codex thread and
durable Goal, waits for the mandatory Claude Opus review to reach its explicit-authorization gate, and
then verifies that the monitor's generic canned approval resumes the Goal into review. It is skipped by
default because a successful run sends the disposable project to Claude and consumes subscription quota.
The harness also covers a known second blocker where a sandboxed Claude process cannot access the user's
credentials; see [`docs/failure-cases.md`](docs/failure-cases.md).

```sh
RUN_CODEX_GOAL_INTEGRATION=1 \
CODEX_INTEGRATION_AUTHORIZE_EXTERNAL_REVIEW=1 \
.test-venv/bin/pytest -m integration -s tests/integration/test_canned_approval_goal.py
```

## Development

```sh
.venv/bin/pytest
```

The app-server API is experimental. Regenerate its schema after upgrading Codex:

```sh
schema_dir="$(mktemp -d)"
codex app-server generate-json-schema --experimental --out "$schema_dir"
```
