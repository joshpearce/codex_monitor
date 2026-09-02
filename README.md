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
codex-goal-monitor print-service
codex-goal-monitor install
codex-goal-monitor uninstall
```

On Linux, `install` enables `codex-goal-monitor.timer` for the current user. Run
`loginctl enable-linger "$USER"` separately if it should operate while the user is logged out.

## Reconciliation behavior

Each invocation:

1. Takes a non-blocking per-user lock; overlapping runs exit successfully.
2. Runs `codex app-server daemon start` and waits for its Unix socket.
3. Connects with WebSocket compression disabled and initializes the protocol.
4. Records each exact thread and Goal before changing it.
5. Resumes unloaded threads; resume may start an active Goal automatically.
6. Changes `paused`, `blocked`, `usageLimited`, or `budgetLimited` back to `active`.
7. Starts a continuation when the Goal is active but the thread is idle.
8. Accepts approval and user-input requests received during a short drain window.
9. Writes private state and an append-only audit log under `~/.local/state/codex-monitor/`.

A configured `goal_objective` is an identity check. A mismatch fails that project without modifying it.
Completed Goals and projects without a Goal are left alone. A 240-second continuation cooldown prevents
duplicate starts while permitting the next regular five-minute invocation to recover an idle Goal.

See [`config/projects.example.toml`](config/projects.example.toml) for all common settings.

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
