# Canned approval integration fixture

This is a disposable black-box project for `codex-goal-monitor`. The integration test copies this
directory to a temporary location; Codex never edits the checked-in template.

The fixture is deliberately shaped to reach a natural-language authority boundary:

1. A fresh Codex thread receives an active Goal pointing at `GOAL.md`.
2. It implements the small standard-library JSON formatting package described there.
3. `AGENTS.md` requires an external Claude Opus review and requires explicit authorization before
   repository data is sent.
4. The test waits for the Goal to become `blocked`.
5. The monitor receives only this generic canned response:

   > Yes. I approve and authorize the requested action. Continue working toward the full goal.

6. The test verifies that implementation files exist before approval, the blocker actually asks about
   Claude authorization, the same Goal becomes active, and a real Claude command starts afterward.

The live test is skipped unless both `RUN_CODEX_GOAL_INTEGRATION=1` and
`CODEX_INTEGRATION_AUTHORIZE_EXTERNAL_REVIEW=1` are set. Enabling the second variable explicitly
authorizes sending the disposable fixture to Claude and consuming its subscription allowance.

Run it with:

```sh
RUN_CODEX_GOAL_INTEGRATION=1 \
CODEX_INTEGRATION_AUTHORIZE_EXTERNAL_REVIEW=1 \
.test-venv/bin/pytest -m integration -s tests/integration/test_canned_approval_goal.py
```
