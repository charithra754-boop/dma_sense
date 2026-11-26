"""Turn results/*.jsonl into the paper's data files, tables and numbers.

Outputs
  paper/data/{burst,knee,stall,energy,policy_drop,policy_adapt,policy_edf}.csv
  paper/generated/numbers.tex     one macro per number, each naming its source
  results/summary.json            everything above plus fits, matches and intervals
Equal budget: for each policy and frame rate, frame dropping takes the delta from its grid whose
mean DRAM rate and total energy are closest to the policy's; the match error is reported and checked
against validation.equal_bw_tol_pct / equal_energy_tol_pct. Intervals: 95% paired bootstrap over clips.
Usage: python -I sim/analyze.py
"""
import json
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sim"))
import dmasense as d  # noqa: E402

RES, DATA, GEN = ROOT / "results", ROOT / "paper" / "data", ROOT / "paper" / "generated"
NAMES = {"drop": "Drop", "fp": "FP", "rr": "RR", "cap": "Capped", "adapt": "Adaptive", "edf": "Deadline"}


def load(name):
    p = RES / f"{name}.jsonl"
    return [json.loads(x) for x in open(p)] if p.exists() else []


def mean(xs):
    xs = [x for x in xs if x == x]
    return st.mean(xs) if xs else float("nan")


def boot_ci(diffs, reps, seed=0):
    rng = random.Random(seed)
    n = len(diffs)
    ms = sorted(st.mean(rng.choices(diffs, k=n)) for _ in range(reps))
    return st.mean(diffs), ms[int(0.025 * reps)], ms[int(0.975 * reps) - 1]


def write_csv(name, header, rows):
    DATA.mkdir(parents=True, exist_ok=True)
    with open(DATA / name, "w") as f:
        f.write(",".join(header) + "\n")
        for r in rows:
            f.write(",".join(f"{x:.4g}" if isinstance(x, float) else str(x) for x in r) + "\n")


def main():
    cfg = d.load_config()
    pred = json.loads((RES / "predictions.json").read_text())
    reps = cfg["validation"]["bootstrap_reps"]
    tol_b = cfg["validation"].get("equal_bw_tol_pct", 5.0)
    tol_e = cfg["validation"].get("equal_energy_tol_pct", 5.0)
    S, M = {}, {}            # summary, macros (name -> (value, source))

    # ---- burst sweep: H1 ----
    burst = load("burst")
    if burst:
        g = defaultdict(list)
        for m in burst:
            g[(m["policy"], m["burst_B"])].append(m)
        Bs = sorted({m["burst_B"] for m in burst})
        pols = ("fp", "rr", "cap", "adapt")
        write_csv("burst.csv", ["burst", *pols],
                  [[B, *[mean([m["imu_p99_us"] for m in g[(p, B)]]) for p in pols]] for B in Bs])
        S["H1"] = {}
        for p in pols:
            ys = [mean([m["imu_p99_us"] for m in g[(p, B)]]) * 1e3 for B in Bs]   # ns
            mx, my = st.mean(Bs), st.mean(ys)
            slope = sum((x - mx) * (y - my) for x, y in zip(Bs, ys)) / sum((x - mx) ** 2 for x in Bs)
            ss_res = sum((y - (my + slope * (x - mx))) ** 2 for x, y in zip(Bs, ys))
            ss_tot = sum((y - my) ** 2 for y in ys) or 1e-12
            S["H1"][p] = {"slope_ns_per_B": slope, "r2": 1 - ss_res / ss_tot,
                          "pred_slope": pred["H1_slope_pred_ns_per_B"].get(p),
                          "p99_us": dict(zip(Bs, [y / 1e3 for y in ys]))}
        tab = {}
        for p in ("fp", "rr", "cap"):
            ms = g[(p, 1024)]
            tab[p] = {"bound_us": pred["wait_bound_ns"]["1024"][p] / 1e3,
                      "p99_us": mean([m["imu_p99_us"] for m in ms]),
                      "max_us": max(m["imu_max_us"] for m in ms)}
            for k, v in tab[p].items():
                kn = {"bound_us": "Bound", "p99_us": "PNinetyNine", "max_us": "Max"}[k]
                M[f"pred{p.upper()}{kn}"] = (v, "results/burst.jsonl, predictions.json")
        S["pred_table"] = tab
        for p in pols:
            M[f"hOne{p.capitalize()}Slope"] = (S["H1"][p]["slope_ns_per_B"], "results/burst.jsonl (fit)")

    # ---- fps sweep: knee ----
    fps = load("fps")
    if fps:
        g = defaultdict(list)
        for m in fps:
            g[m["fps"]].append(m)
        F = sorted(g)
        rows = [[f, mean([m["util"] for m in g[f]]), mean([m["drop_rate"] for m in g[f]]),
                 mean([m["mean_f_MHz"] for m in g[f]]), mean([m["age_mean_ms"] for m in g[f]])] for f in F]
        write_csv("knee.csv", ["fps", "rho", "drop", "f_MHz", "age_ms"], rows)
        knee = None   # first frame rate where more than 1% of frames drop (linear interpolation)
        for (f0, _, d0, *_), (f1, _, d1, *_) in zip(rows, rows[1:]):
            if d0 <= 0.01 < d1:
                knee = f0 + (0.01 - d0) / (d1 - d0) * (f1 - f0)
                break
        S["knee"] = {"fps": knee, "rows": rows, "pred_fps_1024": pred["knee_fps"]["1024"]}
        M["kneeFps"] = (knee, "results/fps.jsonl (drop rate crosses 1%)")
        M["kneePredFps"] = (pred["knee_fps"]["1024"], "results/predictions.json")

    # ---- equal-budget comparison: H2, H3 ----
    pol = load("policy")
    if pol:
        by = defaultdict(dict)       # (fps, policy, delta) -> clip -> metrics
        for m in pol:
            by[(m["fps"], m["policy"], m["drop_frac_target"])][m["clip_seed"]] = m
        S["policy"] = {}
        for f in sorted({k[0] for k in by}):
            deltas = sorted(k[2] for k in by if k[0] == f and k[1] == "drop")
            res = {}
            for p in ("fp", "rr", "cap", "adapt", "edf"):
                pc = by[(f, p, 0.0)]
                bw_p = mean([m["dram_GBps"] for m in pc.values()])
                e_p = mean([m["energy_J"] for m in pc.values()])

                def dist(dl):
                    dc = by[(f, "drop", dl)]
                    return (abs(mean([m["dram_GBps"] for m in dc.values()]) - bw_p) / bw_p
                            + abs(mean([m["energy_J"] for m in dc.values()]) - e_p) / e_p)
                dl = min(deltas, key=dist)
                dc = by[(f, "drop", dl)]
                err_b = abs(mean([m["dram_GBps"] for m in dc.values()]) - bw_p) / bw_p * 100
                err_e = abs(mean([m["energy_J"] for m in dc.values()]) - e_p) / e_p * 100
                clips = sorted(set(pc) & set(dc))
                out = {"delta": dl, "match_err_bw_pct": err_b, "match_err_energy_pct": err_e,
                       "matched": err_b <= tol_b and err_e <= tol_e, "n_clips": len(clips)}
                for key, scale in (("imu_p99_us", 1), ("age_mean_ms", 1), ("drop_rate", 100),
                                   ("energy_per_frame_mJ", 1)):
                    a = [pc[c][key] * scale for c in clips]
                    b = [dc[c][key] * scale for c in clips]
                    pairs = [(x, y) for x, y in zip(a, b) if x == x and y == y]
                    md, lo, hi = boot_ci([x - y for x, y in pairs], reps)
                    pm, plo, phi = boot_ci([x for x, _ in pairs], reps)
                    dm, dlo, dhi = boot_ci([y for _, y in pairs], reps)
                    out[key] = {"policy": [pm, plo, phi], "drop": [dm, dlo, dhi], "diff": [md, lo, hi]}
                res[p] = out
            S["policy"][f] = res
        # table and figure at the frame rate closest to the knee
        fk = min(S["policy"], key=lambda f: abs(f - (S.get("knee", {}).get("fps") or 120)))
        S["policy_table_fps"] = fk
        W = {60: "Sixty", 120: "OneTwenty", 180: "OneEighty", 90: "Ninety", 150: "OneFifty", 240: "TwoForty"}
        PN = {"fp": "FP", "rr": "RR", "cap": "Cap", "adapt": "Adapt", "edf": "Edf"}
        for f, res in S["policy"].items():
            w = W.get(int(f), f"F{int(f)}".replace("0", "o"))
            src = f"results/policy.jsonl ({f} fps)"
            M[f"imuDrop{w}"] = (res["fp"]["imu_p99_us"]["drop"][0], src)
            M[f"ageDrop{w}"] = (res["fp"]["age_mean_ms"]["drop"][0], src)
            M[f"dropDrop{w}"] = (res["fp"]["drop_rate"]["drop"][0], src)
            for p, o in res.items():
                n = PN[p]
                M[f"imu{n}{w}"] = (o["imu_p99_us"]["policy"][0], src)
                M[f"age{n}{w}"] = (o["age_mean_ms"]["policy"][0], src)
                M[f"drop{n}{w}"] = (o["drop_rate"]["policy"][0], src)
                M[f"dImu{n}{w}"] = (o["imu_p99_us"]["diff"][0], src + ", minus matched dropping")
                M[f"dAge{n}{w}"] = (o["age_mean_ms"]["diff"][0], src + ", minus matched dropping")
                M[f"dlt{n}{w}"] = (o["delta"], src + ", matched delta")
        M["nClips"] = (len({m["clip_seed"] for m in pol}), "traces/era_mk.csv")
        M["nRuns"] = (len(burst) + len(fps) + len(pol), "results/*.jsonl")
        M["policyFps"] = (fk, "results/policy.jsonl")
        ref = S["policy"][fk]
        dl_rows = []
        for dl in sorted(k[2] for k in by if k[0] == fk and k[1] == "drop"):
            dc = by[(fk, "drop", dl)].values()
            dl_rows.append([mean([m["drop_rate"] for m in dc]) * 100, mean([m["age_mean_ms"] for m in dc])])
        write_csv("policy_drop.csv", ["drop", "age"], dl_rows)
        for p, fn in (("adapt", "policy_adapt.csv"), ("edf", "policy_edf.csv")):
            write_csv(fn, ["drop", "age"], [[ref[p]["drop_rate"]["policy"][0], ref[p]["age_mean_ms"]["policy"][0]]])
        # stall shares and energy, at the table's frame rate
        srows, erows = [], []
        for p in ("drop", "fp", "rr", "cap", "adapt", "edf"):
            key = (fk, p, ref["fp"]["delta"] if p == "drop" else 0.0)
            ms = by[key].values()
            tot = defaultdict(float)
            for m in ms:
                for mm in m["stall_ns"].values():
                    for c, v in mm.items():
                        tot[c] += v
            s_all = sum(tot.values()) or 1
            srows.append([NAMES[p], *[100 * tot[c] / s_all for c in ("arb", "self", "burst", "cache", "dram")]])
            erows.append([NAMES[p], mean([m["energy_per_frame_mJ"] for m in ms]),
                          mean([m["mean_f_MHz"] for m in ms])])
        write_csv("stall.csv", ["policy", "arb", "self", "burst", "cache", "dram"], srows)
        write_csv("energy.csv", ["policy", "energy", "f_MHz"], erows)
        S["stall"], S["energy"] = srows, erows

    abl = load("ablation")
    if abl:
        g = defaultdict(list)
        for m in abl:
            (k, v), = [(k, v) for k, v in m["overrides"].items() if k != "camera.fps"]
            g[(k, str(v), m["policy"])].append(m)
        S["ablation"], rows = {}, []
        for (k, v) in sorted({(k, v) for k, v, _ in g}, key=lambda x: (x[0], float(x[1]) if x[1].replace(".", "").isdigit() else 0)):
            res = {p: {"imu_p99_us": mean([m["imu_p99_us"] for m in g[(k, v, p)]]),
                       "age_ms": mean([m["age_mean_ms"] for m in g[(k, v, p)]]),
                       "drop_pct": 100 * mean([m["drop_rate"] for m in g[(k, v, p)]])}
                   for p in ("fp", "cap", "adapt", "edf") if g[(k, v, p)]}
            S["ablation"][f"{k}={v}"] = res
            if k == "sim.split_B":
                r = res["adapt"]
                rows.append(f"\\texttt{{{k.split('.')[1]}}} = {v} & -- & -- & {r['imu_p99_us']:.2f} & -- & "
                            f"-- & -- & {r['age_ms']:.1f} & --\\\\")
                continue
            rows.append(f"\\texttt{{{k.split('.')[1]}}} = {v} & " + " & ".join(f"{res[p]['imu_p99_us']:.2f}" for p in ("fp", "cap", "adapt", "edf"))
                        + " & " + " & ".join(f"{res[p]['age_ms']:.1f}" for p in ("fp", "cap", "adapt", "edf")) + "\\\\")
        S["ablation_rows"] = [r.replace("_", "\\_") for r in rows]
    pin = load("pinned")
    if pin:
        g = defaultdict(list)
        for m in pin:
            g[(m["policy"], m["burst_B"], m["fps"])].append(m)
        S["pinned_table"] = {p: {"p99_us": mean([m["imu_p99_us"] for m in g[(p, 1024, 60)]]),
                                 "max_us": max(m["imu_max_us"] for m in g[(p, 1024, 60)])} for p in ("fp", "rr", "cap")}
        prow = [[f, mean([m["util"] for m in g[("fp", 1024, f)]]), mean([m["drop_rate"] for m in g[("fp", 1024, f)]])]
                for f in sorted({k[2] for k in g if k[0] == "fp" and k[1] == 1024})]
        kp = None
        for (f0, _, d0), (f1, _, d1) in zip(prow, prow[1:]):
            if d0 <= 0.01 < d1:
                kp = f0 + (0.01 - d0) / (d1 - d0) * (f1 - f0)
                break
        S["pinned_knee_fps"], S["pinned_rows"] = kp, prow
        M["kneePinnedFps"] = (kp, "results/pinned.jsonl (drop rate crosses 1%, clock at f_max)")
        for p in ("fp", "rr", "cap"):
            M[f"pin{p.upper()}PNinetyNine"] = (S["pinned_table"][p]["p99_us"], "results/pinned.jsonl")
        if fps:
            krows = []
            pr = {r[0]: r for r in prow}
            for r in S["knee"]["rows"]:
                q = pr.get(r[0], [r[0], float("nan"), float("nan")])
                krows.append([r[0], r[1], r[2], q[1], q[2]])
            write_csv("knee.csv", ["fps", "rho", "drop", "rho_pinned", "drop_pinned"], krows)

    def ci(t, nd=2):
        return f"{t[0]:.{nd}f} [{t[1]:.{nd}f}, {t[2]:.{nd}f}]"

    if "pred_table" in S:
        rows = [f"{lab} & {S['pred_table'][p]['bound_us']:.2f} & {S['pred_table'][p]['p99_us']:.2f} & "
                f"{S['pred_table'][p]['max_us']:.2f}"
                + (f" & {S['pinned_table'][p]['p99_us']:.2f}" if "pinned_table" in S else "") + r"\\"
                for p, lab in (("fp", "Fixed priority"), ("rr", "Round-robin"), ("cap", "Burst-capped"))]
        (GEN / "tab_pred.tex").write_text("\n".join(rows) + "\n")
    if "policy" in S:
        fk = S["policy_table_fps"]
        ref = S["policy"][fk]
        f0 = ref["fp"]
        rows = [f"Frame dropping ($\\delta=0$) & {ci(f0['imu_p99_us']['drop'])} & {ci(f0['age_mean_ms']['drop'], 1)} & "
                f"{ci(f0['drop_rate']['drop'], 1)} & {ci(f0['energy_per_frame_mJ']['drop'])}\\\\"]
        for p, lab in (("rr", "Fixed 1 KB, RR"), ("cap", "Burst-capped"), ("adapt", "Adaptive split"),
                       ("edf", "Age/deadline")):
            o = ref[p]
            mark = "" if o["matched"] else "$^\\dagger$"
            rows.append(f"{lab}{mark} ($\\delta={o['delta']:.1f}$) & {ci(o['imu_p99_us']['policy'])} & "
                        f"{ci(o['age_mean_ms']['policy'], 1)} & {ci(o['drop_rate']['policy'], 1)} & "
                        f"{ci(o['energy_per_frame_mJ']['policy'])}\\\\")
        (GEN / "tab_policy.tex").write_text("\n".join(rows) + "\n")
        # differences vs matched dropping, all frame rates (report table)
        rows = []
        for f, res in sorted(S["policy"].items(), key=lambda x: float(x[0])):
            for p, o in res.items():
                if p == "fp":
                    continue
                rows.append(f"{f} & {NAMES[p]} & {o['delta']:.1f} & {o['match_err_bw_pct']:.1f} / "
                            f"{o['match_err_energy_pct']:.1f}{'' if o['matched'] else '$^\\dagger$'} & "
                            f"{ci(o['imu_p99_us']['diff'])} & {ci(o['age_mean_ms']['diff'], 1)}\\\\")
        (GEN / "tab_diff.tex").write_text("\n".join(rows) + "\n")
    if "ablation" in S:
        (GEN / "tab_ablation.tex").write_text("\n".join(S["ablation_rows"]) + "\n")

    (RES / "summary.json").write_text(json.dumps(S, indent=1, default=str))
    M["nRuns"] = (sum(len(load(n)) for n in ("burst", "fps", "policy", "ablation", "pinned")), "results/*.jsonl")
    for k in [k for k in M if k.startswith(("dImu", "dAge"))]:
        v, src = M[k]
        M[k + "Abs"] = (abs(v), src + " (absolute value)")

    def fmt(v):
        if v is None:
            return "--"
        if isinstance(v, float):
            if abs(v) >= 1000:
                return f"{v:,.0f}".replace(",", "{,}")
            return f"{v:.3g}"
        return str(v)
    GEN.mkdir(parents=True, exist_ok=True)
    with open(GEN / "numbers.tex", "w") as f:
        f.write("% Generated by sim/analyze.py. Do not edit.\n")
        for k, (v, src) in sorted(M.items()):
            val = fmt(v)
            f.write(f"\\newcommand{{\\{k}}}{{{val}}} % {src}\n")
    print(json.dumps({k: v for k, v in S.items() if k in ("H1", "pred_table", "knee")}, indent=1, default=str)[:3000])


if __name__ == "__main__":
    main()
