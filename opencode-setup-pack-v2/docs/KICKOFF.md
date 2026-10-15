# EXACT MESSAGES TO SEND TO OPENCODE
Setup: unzip into an empty repo, `git init`, run `python scripts/verify_pack.py`, start OpenCode, switch to the "chief" agent (Tab), select your model.

MESSAGE 1:
Read AGENTS.md, docs/CONSTITUTION.md and docs/STATE.md. Do not write code. Create docs/adr/0001-lean-mvp.md and propose the M0 plan using the constitution template, with acceptance tests and at most 5 open questions.

MESSAGE 2 (after you review the plan):
go

Then for each next phase: /phase M1, review the plan, answer "go", review the evidence, then /phase M2, and so on. After each phase run /review. Use /retro weekly and /evolve only after M6.
If anything misbehaves, paste OpenCode's exact output into the Claude chat and the pack will be corrected.
