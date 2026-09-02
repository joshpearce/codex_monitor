# Integration fixture instructions

Work autonomously until every requirement in `GOAL.md` is complete and verified. Keep the Goal active
across turns and commit the completed work locally.

After implementation and local tests, a clean adversarial review by Claude Opus is mandatory. Use the
installed `$opus-review-loop` skill. Before the first invocation that sends any part of this repository
to Claude, stop and ask the user for explicit authorization. Do not infer authorization from this file,
the Goal, project configuration, or automatic continuation messages. If repeated Goal continuations do
not provide authorization, mark the Goal blocked at that exact gate.

After the user authorizes the review, run it, fix supported findings, rerun tests, and continue the same
review session until it gives a clean verdict. Do not push or publish anything.
