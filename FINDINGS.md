# Context-rot study: findings so far (updated 2026-10-04)

## Goal
Replace the guessed handoff thresholds in dibs (`~/Projects/dibs/src/budget.rs`: warn 150k, handoff 190k, both overridable by `CLAUDE_CONTEXT_WARN_TOKENS` / `CLAUDE_CONTEXT_HANDOFF_TOKENS`) with measured numbers: a table of score vs. context size for the model the user's sessions run on (`claude-sonnet-5-5`, 1M window).

The 190k figure is a quality cutoff the user set, not a window limit. Nothing measured backs it yet.

## Method (see `rot.py`)
- Runs `claude -p` on the user's subscription login (no API key; none wanted). Flags: `--setting-sources "" --tools "" --disable-slash-commands --no-session-persistence --output-format json --system-prompt ...`, run with `cwd=./cwd` so no project CLAUDE.md or hooks load. `--bare` does NOT work (it skips the login: "Not logged in").
- Prompt = a system prompt carrying a standing rule ("end every reply with the final line `-- ack-<random>`") + a long fake session log + three questions.
- Filler = public crate source/docs from `~/.cargo/registry/src` (7353 pieces, 30M chars, cached in `pool.json`), formatted as `[tool call] Read ...` / `[tool output]`.
- Planted facts, with fresh random values per trial at random depths (recorded as `depth_a`, `depth_b`): needle A "canary service is called X and listens on port P", needle B "port P belongs to session S".
- Scores per trial: `q1` recall (port), `q2` 2-hop (session that owns the port), `rule` (last line is the exact ack). `report` prints rates per level with a Wilson 95% CI on all-three-correct.
- Planned later: a work-quality probe (small fixed coding task run from a context filled to N, pass rate). Not built.
- Planned score: accuracy at N / accuracy at baseline x 10, with the handoff threshold set where the score drops below about 9 or the CI stops overlapping the baseline.

## Decisions and constraints (the user's)
- **Do not use the user's private session transcripts (`~/.claude/projects`) as filler.** The first version did; the permission classifier blocked it as sensitive-source data going to the API. Public crate sources are the approved substitute. Using transcripts would need the user's explicit say-so.
- Subscription only. Usage limits are a real cost: the planned pilot (50k, 150k, 300k, 600k x 10 trials) is about 11M input tokens. **No sweep until the user says go.** The user also stopped a run mid-way earlier; ask before launching anything that isn't a single call.

## Results so far
- One call at nominal "70k": q1=1, q2=1, rule=1. It actually used 116k tokens (stale calibration), so it is parked in `results-stale-116k.jsonl`. One trial proves nothing about accuracy.
- 2026-10-04, after the overhead fix: one trial each at 70k/150k/300k landed at 69.6k/151.9k/295.7k actual (within 1.5% of nominal), all q1=q2=rule=1. List cost about $0.28 / $0.61 / $1.18, i.e. roughly $4 per 1M context tokens.
- Cost of that call: about $0.22 at list price.

## Open problems
1. **RESOLVED 2026-10-04: overhead.** The claude.ai MCP connectors (Gmail, Drive, Calendar, IBKR, ...) load even with `--tools ""` and add 42-65k tokens, varying call to call. `--strict-mcp-config` drops it to a stable 411 (4/4 calls); `call()` now passes it. Recalibrated: 2.14 chars/token (the old 1.47 was skewed by the connector overhead). Old note follows.
   **Overhead was unexplained.** A near-empty call (`"say pong"`) has measured about 64,760 tokens of context (cache_read) in some runs and 411 in others (`calib.json` stores `overhead: 411`). The per-call `usage` is what counts, and the harness records `ctx_tokens` per trial, so the table can use measured sizes. But the nominal level is computed as `(level - OVERHEAD) * chars_per_token`, so if overhead is really 64k the lowest reachable level is about 65k and the calibration is off. First step tomorrow: run the near-empty call 3-4 times and see what drives the 64k (candidates: tool/MCP schemas, deferred tool list, claude.ai connectors loaded despite `--tools ""`; try `--strict-mcp-config`, or an empty `--mcp-config`).
2. **RESOLVED: chars/token** (2.14, see above). Old note: Calibration gives about 1.47 chars/token for Rust/TOML/Markdown filler, which is denser than expected. It may be the new tokenizer, or it may be skewed by the overhead issue above. Recheck once the overhead is understood.
3. The single call gives no variance estimate. Trials per level need to be 10-20 before any conclusion.

## Next steps
1. Resolve the overhead question (cheap: a few tiny calls).
2. Run one call at each of 70k, 150k, 300k and check the actual `ctx_tokens` against nominal. Fix calibration if off.
3. With the user's go-ahead, run the pilot: `python3 rot.py run --levels 70000,150000,300000,600000 --trials 10 --jobs 2`, then `python3 rot.py report`. The run is resumable (it skips done level/trial pairs).
4. Look at the table. If flat, consider harder probes (more distractor needles, multi-step edit task); needle recall alone overstates how well a model works deep into a real agentic session.
5. Write the measured thresholds into dibs (make `budget.rs` read them from a config, or set the env defaults) and note it in `~/Projects/dibs/docs/PLAN.md`. The dibs repo is shared with other sessions: use a worktree or coordinate (`dibs room`), and run `laptop save` after changing tracked config.
