# context-rot

Measures how Claude's accuracy falls as context grows, to set the handoff thresholds in dibs (`~/Projects/dibs/src/budget.rs`). Read `FINDINGS.md` first: it has the method, the user's decisions, open problems and next steps.

- Single script `rot.py` (Python is fine here: eval/data work). Subcommands: `pool`, `calibrate`, `run --levels A,B --trials N --jobs J`, `report`. Results append to `results.jsonl`; runs resume.
- It calls `claude -p` on the user's subscription (no API key). Every call costs subscription usage, so run single calls freely but get the user's go-ahead before any multi-trial sweep.
- Filler must stay public (crate sources in `~/.cargo/registry`). Never feed `~/.claude/projects` transcripts or other private files into it; a hook blocked that once, and it needs the user's explicit approval.
- Run the model with `cwd=./cwd` and `--setting-sources ""` so no project instructions or hooks add context. Don't use `--bare` (it breaks login).
- Record actual `ctx_tokens` from `usage` for every trial; report against that, not the nominal level.
- Known unresolved issue: fixed per-call overhead measured as both about 64k and 411 tokens. Resolve it before trusting the calibration.
- Changing dibs thresholds touches a repo other sessions use: coordinate via `dibs room` / a worktree, and `laptop save` after changing tracked config.
