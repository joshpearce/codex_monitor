# Known recovery cases

## Claude review cannot access credentials inside the sandbox

A Claude Opus review has two separate gates that must not be conflated:

1. The Codex agent asks for permission to transmit repository-derived material to Claude and consume
   subscription usage.
2. After receiving that permission, Codex may try to launch `claude` inside its command sandbox. The
   process cannot reliably access the user's stored Claude credentials there. Codex can then stop or
   block again without ever starting a real `claude -p` review.

The recovery for the second gate is a monitor-side classifier and deterministic instruction telling the
existing Codex session to run the already-authorized Claude Opus review outside the sandbox and not to
infer that the user must log in from the sandboxed authentication result. For this recognized failure
only, the monitor attempts to start the resumed turn with `sandbox: danger-full-access`. Testing showed
that this does **not** upgrade a thread originally created with a sandboxed execution profile: Codex then
reports that no host-side runner is available. A session that must run Claude reliably therefore needs to
be created with host-level access from the outset. The integration harness does this explicitly. The
classifier also recognizes Codex's follow-up “host-side command runner” and “host-side execution
capability” blocker wording, but an existing sandbox-only thread may need replacement rather than another
continuation.

The suite deliberately distinguishes `command -v claude` readiness probes from an actual `claude -p`
or `claude --print` invocation. It also keeps its thread manifest after a failed run, so test-code changes
resume the blocked session instead of rebuilding the fixture.
