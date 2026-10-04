#!/usr/bin/env python3
"""Context-rot pilot: how does Claude's accuracy fall as session context grows?

Runs `claude -p` (subscription auth, no API key) on prompts padded to N tokens
with public crate source/docs from ~/.cargo/registry (formatted as a tool-call
log), with facts planted at random depths. No private transcripts are used.

  rot.py pool                       build filler pool
  rot.py calibrate                  measure chars/token for the filler
  rot.py run --levels 50000,150000 --trials 10 [--jobs 2]
  rot.py report                     score table from results.jsonl
"""
import argparse, json, os, random, re, subprocess, statistics, math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent
POOL = HERE / "pool.json"
RESULTS = HERE / "results.jsonl"
CALIB = HERE / "calib.json"
MODEL = os.environ.get("ROT_MODEL", "claude-opus-5-5")
OVERHEAD = json.loads((HERE / "calib.json").read_text()).get("overhead", 0) if (HERE / "calib.json").exists() else 0
PROBE = int(os.environ.get("ROT_PROBE", 3))  # 1: two needles; 2: superseded port + decoys; 3: scattered-spec coding task
REG = Path.home() / ".cargo/registry/src"

SECRET = re.compile(r"(sk-[A-Za-z0-9]{10,}|BEGIN [A-Z ]*PRIVATE|ghp_|xox[bp]-)")


def build_pool(limit_chars=30_000_000):
    files = [p for p in REG.glob("*/*/**/*") if p.suffix in (".rs", ".md", ".toml") and p.is_file()]
    random.Random(1).shuffle(files)
    pool, total = [], 0
    for f in files:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        if SECRET.search(text) or len(text) < 400:
            continue
        rel = "/".join(f.parts[-3:])
        text = text[:6000]
        pool.append(f"[tool call] Read {rel}\n[tool output]\n{text}")
        total += len(pool[-1])
        if total > limit_chars:
            break
    POOL.write_text(json.dumps(pool))
    print(f"pool: {len(pool)} pieces, {total/1e6:.1f}M chars")


def words(rng, n):
    syll = ["ka", "to", "ri", "ve", "mu", "sa", "lo", "ne", "di", "po", "ba", "zu"]
    return "".join(rng.choice(syll) for _ in range(n))


def make_prompt(level_tokens, seed, cpt):
    rng = random.Random(seed)
    pool = json.loads(POOL.read_text())
    target = int((level_tokens - OVERHEAD) * cpt)
    chunks, size = [], 0
    while size < target:
        p = rng.choice(pool)
        chunks.append(p)
        size += len(p) + 2
    svc = words(rng, 3)
    p_old, port, *decoy_ports = rng.sample(range(20000, 60000), 5)
    owner, owner_old, *decoy_owners = [f"{words(rng, 2)}-{rng.randint(10, 99)}" for _ in range(5)]
    ack = words(rng, 3)
    rec = "[user] Decision for the record:"
    d_old = rng.uniform(0.05, 0.6)
    da, db = rng.uniform(d_old + 0.05, 0.95), rng.uniform(0.05, 0.95)
    needles = [
        (d_old, f"{rec} the canary service is called {svc} and it listens on port {p_old}."),
        (da, f"[user] Update: the canary service {svc} has moved from port {p_old} to port {port}; port {p_old} is retired."),
        (db, f"{rec} port {port} belongs to session {owner}; nobody else may use it."),
        (rng.uniform(0.05, 0.95), f"{rec} port {p_old} belongs to session {owner_old}; nobody else may use it."),
    ]
    for role, q, o in zip(["staging", "metrics", "backup"], decoy_ports, decoy_owners):
        needles.append((rng.uniform(0.05, 0.95), f"{rec} the {role} service is called {words(rng, 3)} and it listens on port {q}."))
        needles.append((rng.uniform(0.05, 0.95), f"{rec} port {q} belongs to session {o}; nobody else may use it."))
    for depth, n in sorted(needles, reverse=True):
        chunks.insert(int(len(chunks) * depth), n)
    body = "\n\n".join(chunks)
    system = (
        "You are a coding assistant reading a long session log. "
        f"Rule from the user, always in force: end every reply with the exact final line `-- ack-{ack}`."
    )
    user = (
        "Below is the log of an earlier working session.\n\n<log>\n" + body + "\n</log>\n\n"
        "Answer from the log. Use exactly this format, one line each:\n"
        "Q1: which port does the canary service listen on now? (number only)\n"
        "Q2: which session owns the port the canary service listens on now? (name only)\n"
        "Q3: one sentence on what the log is mostly about.\n"
        "Then finish as your standing rule requires."
    )
    truth = {"q1": str(port), "q2": owner, "ack": f"-- ack-{ack}", "depth_a": da, "depth_b": db, "depth_old": d_old}
    return system, user, truth


def price_spec(rng):
    n_old = rng.randint(5, 12)
    return {
        "n_old": n_old, "d_old": rng.choice([5, 8, 10]),
        "n": n_old + rng.randint(3, 8), "d": rng.choice([12, 15, 20]),
        "letter": rng.choice("BCDFGHKMPRST"), "flat": rng.randint(2, 5) * 50,
        "code": words(rng, 2).upper(), "coupon": rng.choice([7, 11, 13]),
        "rush": rng.randint(3, 15) * 100, "min": rng.randint(5, 20) * 100,
    }


def ref_price(o, sp, mutant=None):
    """Reference pricing; `mutant` drops or reverts one rule, for diagnosing failures."""
    if o["qty"] == 0:
        return 0
    x = o["qty"] * o["unit"]
    n, d = (sp["n_old"], sp["d_old"]) if mutant == "old_bulk" else (sp["n"], sp["d"])
    if mutant != "bulk" and o["qty"] >= n:
        x -= x * d // 100
    if mutant != "loyalty" and o["customer"][:1].upper() == sp["letter"]:
        x -= sp["flat"]
    if mutant != "coupon" and o["coupon"] == sp["code"]:
        x -= x * sp["coupon"] // 100
    if mutant != "rush" and o["rush"]:
        x += sp["rush"]
    if mutant != "min":
        x = max(x, sp["min"])
    return x


MUTANTS = ["old_bulk", "bulk", "loyalty", "coupon", "rush", "min"]


def price_cases(sp, seed):
    rng = random.Random(seed)
    cases = []
    for k in range(40):
        qty = rng.choice([0, 1, 2, sp["n_old"] - 1, sp["n_old"], sp["n_old"] + 1, sp["n"] - 1, sp["n"], sp["n"] + 1,
                          rng.randint(1, 2 * sp["n"])]) if k else 0
        first = sp["letter"] if rng.random() < 0.4 else rng.choice("AEILNOQUWZ")
        cases.append({"qty": qty, "unit": rng.choice([rng.randint(400, 900), rng.randint(900, 5000)]),
                      "customer": first + words(rng, 2), "rush": rng.random() < 0.5,
                      "coupon": rng.choice([None, None, sp["code"], sp["code"], "SAVE" + str(rng.randint(1, 99))])})
    return cases


def make_prompt3(level_tokens, seed, cpt):
    rng = random.Random(seed)
    chunks = filler(rng, level_tokens, cpt)
    sp = price_spec(rng)
    ack = words(rng, 3)
    u = "[user] Requirement for the pricing function we are writing:"
    d_bulk = rng.uniform(0.05, 0.6)
    notes = [
        (rng.uniform(0.02, 0.3), f"{u} it is `price(order)` and returns an int, in cents. An order is a dict with keys qty (int), unit (int, unit price in cents), customer (str), rush (bool), coupon (str or None)."),
        (rng.uniform(0.05, 0.95), f"{u} the starting amount is qty * unit. An order with qty 0 costs 0, no matter what else applies."),
        (d_bulk, f"{u} bulk discount: when qty is at least {sp['n_old']}, take {sp['d_old']}% off."),
        (rng.uniform(d_bulk + 0.05, 0.97), f"[user] Change to the pricing bulk discount: it now starts at qty {sp['n']} (not {sp['n_old']}) and is {sp['d']}% (not {sp['d_old']}%)."),
        (rng.uniform(0.05, 0.95), f"{u} loyalty: customers whose name starts with the letter {sp['letter']} (either case) get a flat {sp['flat']} cents off."),
        (rng.uniform(0.05, 0.95), f"{u} the coupon code {sp['code']} gives {sp['coupon']}% off; any other coupon value is ignored."),
        (rng.uniform(0.05, 0.95), f"{u} rush orders pay a surcharge of {sp['rush']} cents. The surcharge is never discounted."),
        (rng.uniform(0.05, 0.95), f"{u} the minimum charge for any non-empty order is {sp['min']} cents."),
        (rng.uniform(0.05, 0.95), f"{u} apply the steps in this order: bulk discount, loyalty, coupon, rush surcharge, minimum charge. Every percentage discount amount is rounded down to whole cents before it is subtracted."),
    ]
    for depth, n in sorted(notes, reverse=True):
        chunks.insert(int(len(chunks) * depth), n)
    system = (
        "You are a coding assistant reading a long session log. "
        f"Rule from the user, always in force: end every reply with the exact final line `-- ack-{ack}`."
    )
    user = (
        "Below is the log of an earlier working session.\n\n<log>\n" + "\n\n".join(chunks) + "\n</log>\n\n"
        "Task: write the Python function `price(order)` implementing every pricing requirement the user set in the log "
        "(a later change overrides what it changes). Standard library only. Reply with exactly one ```python code block "
        "containing the function, then finish as your standing rule requires."
    )
    truth = {"spec": sp, "seed": seed, "ack": f"-- ack-{ack}", "depth_a": notes[3][0], "depth_b": d_bulk, "depth_old": d_bulk}
    return system, user, truth


def run_code(text, cases):
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.S)
    code = blocks[-1] if blocks else text
    harness = code + "\n\nimport json, sys\nprint(json.dumps([price(o) for o in json.loads(sys.stdin.read())]))\n"
    try:
        r = subprocess.run(["python3", "-I", "-c", harness], input=json.dumps(cases), capture_output=True, text=True, timeout=10)
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return None


def score3(text, truth):
    sp = truth["spec"]
    cases = price_cases(sp, truth["seed"])
    got = run_code(text, cases) or [None] * len(cases)
    want = [ref_price(o, sp) for o in cases]
    ok = sum(g == w for g, w in zip(got, want))
    diag = [m for m in MUTANTS if all(g == ref_price(o, sp, m) for g, o in zip(got, cases))] if ok < len(cases) else []
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    rule = int(bool(lines) and lines[-1].strip("`*") == truth["ack"])
    return {"cases_ok": ok, "pass": int(ok == len(cases)), "rule": rule, "diag": diag or ("" if ok == len(cases) else "other"),
            "q1": int(ok == len(cases)), "q2": 1}


def filler(rng, level_tokens, cpt):
    pool = json.loads(POOL.read_text())
    target = int((level_tokens - OVERHEAD) * cpt)
    chunks, size = [], 0
    while size < target:
        p = rng.choice(pool)
        chunks.append(p)
        size += len(p) + 2
    return chunks


def call(system, user):
    cmd = ["claude", "-p", "--model", MODEL, "--setting-sources", "", "--tools", "", "--disable-slash-commands",
           "--no-session-persistence", "--strict-mcp-config", "--output-format", "json", "--system-prompt", system]
    r = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=1500, cwd=HERE / "cwd")
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-500:] or r.stdout[-500:])
    return json.loads(r.stdout)


def ctx_tokens(out):
    u = out["usage"]
    return u["input_tokens"] + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)


def score(text, truth):
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    def grab(k):
        for l in lines:
            m = re.match(rf"{k}\s*[:.)-]\s*(.*)", l, re.I)
            if m:
                return m.group(1).strip().strip("`*")
        return ""
    return {
        "q1": int(grab("Q1") == truth["q1"]),
        "q2": int(grab("Q2") == truth["q2"]),
        "rule": int(bool(lines) and lines[-1].strip("`*") == truth["ack"]),
    }


def trial(level, i, cpt):
    mk, sc = (make_prompt3, score3) if PROBE == 3 else (make_prompt, score)
    system, user, truth = mk(level, level * 1000 + i, cpt)
    out = call(system, user)
    text = out.get("result", "")
    rec = {"model": MODEL, "probe": PROBE, "level": level, "trial": i, "ctx_tokens": ctx_tokens(out), "depth_a": truth["depth_a"],
           "depth_b": truth["depth_b"], "depth_old": truth["depth_old"], "cost_list_usd": out.get("total_cost_usd"), "answer": text[-400:] if PROBE != 3 else text[-3000:],
           **sc(text, truth)}
    with open(RESULTS, "a") as f:
        f.write(json.dumps(rec) + "\n")
    extra = f" cases={rec['cases_ok']}/40 diag={rec['diag']}" if PROBE == 3 else ""
    print(f"level {level} trial {i}: ctx={rec['ctx_tokens']} q1={rec['q1']} q2={rec['q2']} rule={rec['rule']}{extra}", flush=True)


def cmd_calibrate():
    global OVERHEAD
    OVERHEAD = 0
    base = ctx_tokens(call("You are terse.", "say pong"))
    system, user, _ = make_prompt(100_000, 1, 3.0)
    out = call(system, user)
    cpt = (len(system) + len(user)) / (ctx_tokens(out) - base)
    CALIB.write_text(json.dumps({"chars_per_token": cpt, "overhead": base}))
    print(f"overhead {base}; {len(user)} chars -> {ctx_tokens(out)} tokens, {cpt:.2f} chars/token")


def cmd_run(a):
    cpt = json.loads(CALIB.read_text())["chars_per_token"]
    done = set()
    if RESULTS.exists():
        for l in RESULTS.read_text().splitlines():
            r = json.loads(l)
            if r.get("probe", 1) == PROBE and r.get("model", "claude-sonnet-5-5") == MODEL:
                done.add((r["level"], r["trial"]))
    levels = [int(x) for x in a.levels.split(",")]
    jobs = [(lv, i) for i in range(a.trials) for lv in levels if (lv, i) not in done]
    def go(j):
        try:
            trial(j[0], j[1], cpt)
        except Exception as e:
            print(f"level {j[0]} trial {j[1]} FAILED: {e}", flush=True)
    with ThreadPoolExecutor(a.jobs) as ex:
        list(ex.map(go, jobs))


def wilson(k, n, z=1.96):
    if n == 0:
        return (0, 0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0, c - h), min(1, c + h))


def cmd_report():
    rows = [json.loads(l) for l in RESULTS.read_text().splitlines()]
    by = {}
    for r in rows:
        by.setdefault((r.get("model", "claude-sonnet-5-5"), r.get("probe", 1), r["level"]), []).append(r)
    print(f"{'model':>18} {'probe':>5} {'level':>8} {'ctx(avg)':>9} {'n':>3} {'recall':>7} {'2-hop':>7} {'rule':>7} {'all3':>7}  95% CI (all3)  recall by update depth (early/mid/late)")
    for (md, pr, lv) in sorted(by):
        rs = by[(md, pr, lv)]
        n = len(rs)
        f = lambda k: sum(r[k] for r in rs) / n
        all3 = sum(r["q1"] and r["q2"] and r["rule"] for r in rs)
        lo, hi = wilson(all3, n)
        avg = statistics.mean(r["ctx_tokens"] for r in rs)
        if pr == 3:
            k = sum(r["pass"] for r in rs)
            lo, hi = wilson(k, n)
            fails = [str(r["diag"]) for r in rs if not r["pass"]]
            print(f"{md:>18} {pr:>5} {lv:>8} {avg:>9.0f} {n:>3}  pass {k/n:.2f} [{lo:.2f}, {hi:.2f}]  cases {statistics.mean(r['cases_ok'] for r in rs)/40:.3f}  rule {f('rule'):.2f}  fails: {'; '.join(fails)}")
            continue
        buckets = []
        for a, b in ((0, 1 / 3), (1 / 3, 2 / 3), (2 / 3, 1.01)):
            hit = [r["q1"] for r in rs if a <= r["depth_a"] < b]
            buckets.append(f"{sum(hit)}/{len(hit)}")
        print(f"{md:>18} {pr:>5} {lv:>8} {avg:>9.0f} {n:>3} {f('q1'):>7.2f} {f('q2'):>7.2f} {f('rule'):>7.2f} {all3/n:>7.2f}  [{lo:.2f}, {hi:.2f}]    {' '.join(buckets)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="c", required=True)
    sp.add_parser("pool"); sp.add_parser("calibrate"); sp.add_parser("report")
    r = sp.add_parser("run")
    r.add_argument("--levels", required=True); r.add_argument("--trials", type=int, default=10); r.add_argument("--jobs", type=int, default=2)
    a = ap.parse_args()
    {"pool": build_pool, "calibrate": cmd_calibrate, "run": lambda: cmd_run(a), "report": cmd_report}[a.c]()
