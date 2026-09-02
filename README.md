# Codex Goal Monitor

Codex Goal Monitor keeps selected Codex Goals moving without requiring you to watch them continuously.
Every five minutes, it checks the exact Codex threads you configured, restarts an idle Goal, recovers a
paused or blocked Goal, and answers approval requests encountered during that recovery window.

It is a small, one-shot supervisor rather than a permanently connected service. On macOS it runs as a
LaunchAgent; on Linux it runs as a `systemd --user` timer.

> **Important:** the monitor automatically approves commands, file changes, permissions, and user-input
> requests for configured threads. Use it only with Goals, repositories, instructions, external services,
> and host environments that you trust.

## Quick start

### 1. Check the prerequisites

You need:

- macOS or Linux
- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)
- a Codex CLI that provides `codex app-server daemon` and is compatible with app-server protocol 0.152.1
- an existing Codex thread with a Goal

### 2. Install the monitor

From this repository, run:

```sh
make install
```

This installs or replaces the `codex-goal-monitor` command with `uv tool`.

### 3. Create the configuration

Run the service installer once:

```sh
make install-service
```

On the first run, it creates `~/.config/codex-monitor/projects.toml` with mode `0600` and stops so that
you can replace the example project safely. Edit its `[[project]]` entry:

```toml
[[project]]
name = "my-project"
path = "/absolute/path/to/my-project"
thread_id = "the-exact-codex-thread-id"
goal_objective = "the exact Goal objective"
ensure_goal_running = true
```

Use the exact thread ID shown by the Codex TUI or another trusted source. The project path alone is not a
reliable identity because the thread's recorded working directory may differ from the repository that
contains its Goal. `goal_objective` is an optional but recommended identity check: if the live objective
does not match it exactly, the monitor refuses to modify the thread.

For every project you monitor, add this project-local Codex configuration at
`<project>/.codex/config.toml`:

```toml
approval_policy = "never"
approvals_reviewer = "auto_review"
```

The project must also be trusted by Codex.

### 4. Start monitoring and verify it

Rerun the installer to install the service, reconcile immediately, and schedule future runs every five
minutes:

```sh
make install-service
```

Once the app-server daemon is running, you can verify the configured live state without changing it:

```sh
codex-goal-monitor inspect
```

That is the complete basic setup. If `inspect` reports the expected thread and Goal, the monitor is
ready to keep working on it.

## Everyday commands

The installed service runs reconciliation automatically. These commands are useful for manual checks:

```sh
codex-goal-monitor inspect      # Read live thread and Goal state without changing it
codex-goal-monitor reconcile    # Run one reconciliation immediately
codex-goal-monitor status       # Show the most recently saved monitor state
```

To remove only the scheduled service, while retaining configuration, state, audit history, and logs:

```sh
make uninstall-service
```

To remove the installed command as well:

```sh
make uninstall
```

Use a non-default configuration file by passing `CONFIG` to the service target and `--config` to manual
commands:

```sh
make install-service CONFIG=/path/to/projects.toml
codex-goal-monitor --config /path/to/projects.toml inspect
```

## What happens during reconciliation

Each invocation:

1. Takes a non-blocking per-user lock. An overlapping run exits successfully.
2. Starts `codex app-server daemon` if needed and waits for its Unix socket.
3. Connects to the app-server and reads each configured thread and Goal.
4. Refuses to act if a configured `goal_objective` does not match.
5. Resumes an unloaded thread.
6. Changes a `paused`, `blocked`, `usageLimited`, or `budgetLimited` Goal back to `active`.
7. Starts a continuation when the Goal is active but its thread is idle.
8. Approves requests received during a short drain window.
9. Saves private state and an append-only audit log under `~/.local/state/codex-monitor/`.

Completed Goals and threads without a Goal are left untouched. A 240-second cooldown prevents duplicate
continuations while allowing the next regular run to recover an idle Goal.

## Logs and audit history

Every reconciliation prints compact JSON. Platform service logs are available at:

- macOS: `~/Library/Logs/codex-goal-monitor.log` and
  `~/Library/Logs/codex-goal-monitor.error.log`
- Linux: `journalctl --user -u codex-goal-monitor.service --since today`

The monitor also writes a mode-`0600` JSONL audit stream to
`~/.local/state/codex-monitor/audit.jsonl`. Records include the before and after state, actions, elapsed
time, recovery strategy, and a redacted blocker fingerprint. Raw assistant blocker text is not logged.

Useful outcomes include `recovery-submitted`, `already-active`, `continuation-cooldown`, `complete`, and
`error`. Compare `blockerFingerprint` and `sameBlockerCount` across runs to identify a repeated recovery
loop.

## Configuration and safety controls

The complete documented configuration is in
[`config/projects.example.toml`](config/projects.example.toml). Project-local approval settings are
preferred. The monitor applies approval overrides itself only when `[defaults]` in `projects.toml`
explicitly requests them.

Every handled condition has a `[notices]` switch, enabled by default:

| Conditions | Switches |
| --- | --- |
| Daemon startup; overlapping invocation | `daemon_unavailable`, `overlapping_run` |
| Unloaded thread | `unloaded_thread` |
| Paused, blocked, usage-limited, budget-limited Goals | `paused_goal`, `blocked_goal`, `usage_limited_goal`, `budget_limited_goal` |
| Idle active Goal; already-active thread | `idle_active_goal`, `active_thread` |
| Complete Goal; absent or disabled Goal | `complete_goal`, `missing_or_disabled_goal` |
| Goal-objective identity guard | `objective_mismatch` |
| Natural-language approval; Claude host recovery | `natural_language_approval`, `claude_sandbox_auth` |
| Command, file, and permission approvals | `command_approval`, `file_change_approval`, `permissions_approval` |
| User input; MCP elicitation | `user_input`, `mcp_elicitation` |
| Legacy command and patch approvals | `legacy_exec_approval`, `legacy_patch_approval` |
| Repeated-blocker fingerprinting | `repeated_blocker` |

A disabled state-recovery switch reports `notice-disabled` and leaves that condition untouched. A
disabled protocol switch rejects the automatic response, leaving Codex waiting. In particular, disabling
`objective_mismatch` removes an identity guard, and disabling `overlapping_run` allows concurrent monitor
processes.

### Detailed behavior and limitations

| Condition | Response | Limitation |
| --- | --- | --- |
| App-server daemon is unavailable | Starts it and waits for the socket | Startup failure ends the run as `run-failed` |
| Another invocation holds the lock | Exits successfully | The skipped run does not wait |
| Thread is unloaded | Calls `thread/resume` with configured overrides | Resume may start a Goal turn itself |
| Goal is paused or blocked | Reactivates it and sends the configured affirmative answer | Blocked Goals are treated as authorized unless a specific classifier matches |
| Goal is usage- or budget-limited | Reactivates it and continues if idle | The monitor cannot acquire quota or change account limits |
| Active Goal has an idle thread | Sends a continuation request | The cooldown suppresses duplicate turns |
| Goal and thread are active | Leaves them alone until the next run | It cannot determine whether the active turn is making useful progress |
| Goal is complete or missing | Leaves the thread alone | The monitor never creates or reopens Goals |
| Goal objective differs from configuration | Refuses to modify the thread | Matching is exact |
| Assistant asks for natural-language authorization | Sends the generic affirmative answer | Keyword detection can miss unfamiliar wording |
| Claude lacks authentication or host execution | Requests host credentials and `danger-full-access` | Threads created without host execution may not be upgradeable later |
| Codex requests command, file, or permission approval | Accepts it for the configured thread, for the session when supported | Requested permissions are accepted as supplied by Codex |
| Codex requests structured user input | Selects the first option, or sends the generic affirmative answer | Options are not evaluated semantically |
| MCP server requests elicitation | Accepts with empty content | Cannot satisfy required non-empty structured data |
| The same blocker recurs | Records its fingerprint and count, then retries normal recovery | Repetition does not change strategy or stop automatically |

Unknown assistant messages fall back to normal continuation behavior; there is no general LLM-based
blocker interpreter. Claude-specific recovery signatures and their evidence are documented in
[`docs/failure-cases.md`](docs/failure-cases.md).

## Development

The Makefile is the primary development interface:

```sh
make test
make build
```

Override the `uv` executable when necessary:

```sh
make test UV=/absolute/path/to/uv
make build UV=/absolute/path/to/uv
make install UV=/absolute/path/to/uv
```

The app-server API is experimental. After upgrading Codex, regenerate its schema for investigation with:

```sh
schema_dir="$(mktemp -d)"
codex app-server generate-json-schema --experimental --out "$schema_dir"
```

Protocol investigation notes are preserved in
[`docs/protocol-recovery-notes.md`](docs/protocol-recovery-notes.md).

## Opt-in integration test

The black-box integration suite under
[`integration/canned_approval`](integration/canned_approval) creates a disposable project, Codex thread,
and durable Goal, then exercises the canned-approval recovery flow. It is skipped by default because a
successful run sends the project to Claude and consumes subscription quota.

Run it only after intentionally opting into both external review and quota use:

```sh
RUN_CODEX_GOAL_INTEGRATION=1 \
CODEX_INTEGRATION_AUTHORIZE_EXTERNAL_REVIEW=1 \
uv run --extra test pytest -m integration -s tests/integration/test_canned_approval_goal.py
```
