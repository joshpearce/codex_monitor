# Known recovery cases

## Claude review cannot access credentials inside the sandbox

A Claude Opus review has two separate gates that must not be conflated:

1. The Codex agent asks for permission to transmit repository-derived material to Claude and consume
   subscription usage.
2. After receiving that permission, Codex may try to launch `claude` inside its command sandbox. The
   process cannot reliably access the user's stored Claude credentials there. Codex can then stop or
   block again without ever starting a real `claude -p` review.

The first gate has its own classifier and two-phase deterministic response. When the assistant says that
generic authorization is insufficient, the monitor leaves the Goal blocked and starts a standalone turn
that explicitly names repository-derived materials, Anthropic Claude Opus, and the associated
subscription usage. Reactivating the Goal first is incorrect: Goal machinery prepends
`<codex_internal_context>`, after which the security layer treats the consent as untrusted transcript
data. A later reconciliation reactivates and continues the Goal only after the assistant confirms that
the standalone authorization was accepted.

The continuation after acceptance preserves the thread's sandbox. For monitored turns, configure
`approval_policy = "on-request"` and `approvals_reviewer = "user"`: app-server then routes the Claude
sandbox-escape/network request to the connected monitor, whose approval handler is restricted to exact
configured thread IDs. `auto_review` is the wrong reviewer for this case because it reevaluates the
repository egress from transcript provenance and can reject consent that the Goal agent accepted.
Changing the effective routing to `on-request`/`user` also clears a persisted rejected-authorization
state on the next reconciliation, allowing the existing blocked Goal to retry under the monitor-owned
approval path without hand-editing `runtime.json`.

The recovery for the second gate is a monitor-side classifier and deterministic instruction telling the
existing Codex session to run the already-authorized Claude Opus review outside the sandbox and not to
infer that the user must log in from the sandboxed authentication result. For this recognized failure
only, the monitor attempts to start the resumed turn with `sandbox: danger-full-access`. Testing showed
that this does **not** upgrade a thread originally created with a sandboxed execution profile: Codex then
reports that no host-side runner is available. A session that must run Claude reliably therefore needs to
be created with host-level access from the outset. The integration harness does this explicitly. The
classifier also recognizes Codex's follow-up “host-side command runner” and “host-side execution
capability” blocker wording. Those messages are terminal for automatic recovery: the monitor leaves the
Goal blocked and reports `manual-intervention-required` rather than retrying an execution profile that
cannot be upgraded. The thread must be replaced with one created with host-level access.

The suite deliberately distinguishes `command -v claude` readiness probes from an actual `claude -p`
or `claude --print` invocation. It also keeps its thread manifest after a failed run, so test-code changes
resume the blocked session instead of rebuilding the fixture.
