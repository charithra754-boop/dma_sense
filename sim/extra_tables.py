"""Extra report tables from committed results (no new simulation).

Writes paper/generated/tab_{valid2,valid3,traces,groups,burst,fps,stall,energy}.tex
Usage: python -I sim/extra_tables.py
"""
import csv
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sim"))
import sweep  # noqa: E402

RES, GEN, DATA = ROOT / "results", ROOT / "paper" / "generated", ROOT / "paper" / "data"


def w(name, rows):
    (GEN / f"tab_{name}.tex").write_text("\n".join(r + r"\\" for r in rows) + "\n")


def mean(xs):
    xs = [x for x in xs if x == x]
    return st.mean(xs) if xs else float("nan")


v = json.loads((RES / "validation.json").read_text())
w("valid2", [f"{x['burst']} & {x['busy_ns'] / 1e6:.3f} & {x['busy_pred_ns'] / 1e6:.3f} & {x['err_busy_pct']:.1e} & "
             f"{x['util']:.4f} & {x['rho_pred']:.4f} & {x['err_rho_pct']:.2f}" for x in v["V2_single_master"]])
names = {"fp": "Fixed priority", "rr": "Round-robin", "cap": "Burst-capped"}
w("valid3", [f"{names[x['policy']]} & {x['N']} & {x['burst']} & {x['bound_ns']:.0f} & {x['max_wait_ns']:.0f} & "
             f"{x['tightness']:.2f}" for x in v["V3_blocking_bounds"]])

# traces: per motion group
mk = list(csv.DictReader(open(ROOT / "traces" / "era_mk.csv")))
rows = []
for g, lab in (("hover", "Hover"), ("moving", "Moving"), ("unreliable", "Untrackable")):
    r = [x for x in mk if x["group"] == g]
    clips = {(x["split"], x["id"]) for x in r}
    m = [float(x["mk"]) for x in r]
    mad = [float(x["mad"]) for x in r]
    rows.append(f"{lab} & {len(clips)} & {len(r):,} & {st.mean(m):.3f} & {st.median(m):.3f} & "
                f"{sorted(m)[int(0.9 * len(m))]:.3f} & {st.mean(mad):.2f}".replace(",", "{,}"))
w("traces", rows)

# per-group results at 120 fps
order = [k for k, _ in sweep.era_mk()]          # clip_seed -> (split, id, group)
grp = {i: k[2] for i, k in enumerate(order)}
pol = [json.loads(x) for x in open(RES / "policy.jsonl")]
agg = defaultdict(list)
for m in pol:
    if m["fps"] == 120 and m["drop_frac_target"] == 0.0:
        agg[(m["policy"], grp[m["clip_seed"]])].append(m)
rows = []
for p, lab in (("fp", "Frame dropping / FP"), ("rr", "Round-robin"), ("cap", "Burst-capped"),
               ("adapt", "Adaptive split"), ("edf", "Age/deadline")):
    cells = []
    for g in ("hover", "moving", "unreliable"):
        ms = agg[(p, g)]
        cells.append(f"{mean([x['imu_p99_us'] for x in ms]):.2f} & {mean([x['age_mean_ms'] for x in ms]):.1f}")
    rows.append(f"{lab} & " + " & ".join(cells))
w("groups", rows)

# burst, fps, stall, energy straight from the figure data
b = list(csv.DictReader(open(DATA / "burst.csv")))
w("burst", [f"{r['burst']} & {float(r['fp']):.2f} & {float(r['rr']):.2f} & {float(r['cap']):.2f} & {float(r['adapt']):.2f}" for r in b])
k = list(csv.DictReader(open(DATA / "knee.csv")))
fpsrows = {int(float(r[0])): r for r in json.loads((RES / "summary.json").read_text())["knee"]["rows"]}
w("fps", [f"{r['fps']} & {float(r['rho']):.3f} & {100 * float(r['drop']):.1f} & {fpsrows[int(float(r['fps']))][3]:.1f} & "
          f"{fpsrows[int(float(r['fps']))][4]:.1f} & {float(r['rho_pinned']):.3f} & {100 * float(r['drop_pinned']):.1f}" for r in k])
s = list(csv.DictReader(open(DATA / "stall.csv")))
w("stall", [f"{r['policy']} & " + " & ".join(f"{float(r[c]):.2f}" for c in ("arb", "self", "burst", "cache", "dram")) for r in s])
e = list(csv.DictReader(open(DATA / "energy.csv")))
w("energy", [f"{r['policy']} & {float(r['energy']):.3f} & {float(r['f_MHz']):.1f}" for r in e])
print("ok")

# config file listing for the report appendix (long comment lines wrapped)
import textwrap  # noqa: E402
out = []
for line in (ROOT / "config" / "params.toml").read_text().split("\n"):
    code, _, cmt = line.partition("#")
    if len(line) <= 92 or not cmt:
        out.append(line)
        continue
    out.append(code.rstrip())
    out += ["    " + c for c in textwrap.wrap("#" + cmt, 88)]
(GEN / "params_listing.tex").write_text("{\\scriptsize\n\\begin{verbatim}\n" + "\n".join(out) + "\n\\end{verbatim}\n}\n")
