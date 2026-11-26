"""Run parameter grids in parallel; one JSON line per run in results/<name>.jsonl.

  burst   B_c in burst_sweep_B x {fp, rr, cap, adapt} at 60 fps     -> burst figure, H1 fit, Table pred
  fps     fps_sweep x {fp} at B_c = 1024                             -> knee figure, knee value
  policy  ERA clips x {fp, rr, cap, adapt, edf} at --fps list, B_c = 1024, NPU reads scale with m_k
          (gamma_min = 0.25), plus frame dropping at delta in DROP_GRID for equal-budget matching
Usage: python -I sim/sweep.py burst|fps|policy [--seeds 10] [--seconds 1.0] [--fps 60,120,180]
"""
import argparse
import json
import sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dmasense as d  # noqa: E402

RES = Path(__file__).resolve().parent.parent / "results"


def one(job):
    over, kw = job
    m = d.run(d.load_config(**over), **kw)
    m["overrides"] = over
    if kw.get("mk") is not None:
        m["clip_seed"] = kw["seed"]
    return m


DROP_GRID = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6)


def era_mk():
    import csv
    clips = {}
    for r in csv.DictReader(open(RES.parent / "traces" / "era_mk.csv")):
        clips.setdefault((r["split"], r["id"], r["group"]), []).append(float(r["mk"]))
    return sorted(clips.items())


def grid(name, seeds, seconds, fps_list=(60, 120, 180)):
    cfg = d.load_config()
    jobs = []
    if name == "burst":
        for B in cfg["camera"]["burst_sweep_B"]:
            for pol in ("fp", "rr", "cap", "adapt"):
                for s in range(seeds):
                    jobs.append(({"camera.fps": 60}, dict(policy=pol, burst=B, seed=s, seconds=seconds)))
    elif name == "fps":
        for f in cfg["camera"]["fps_sweep"]:
            for s in range(seeds):
                jobs.append(({"camera.fps": f}, dict(policy="fp", burst=1024, seed=s, seconds=seconds)))
    elif name == "policy":
        for f in fps_list:
            for ci, ((split, cid, grp), mk) in enumerate(era_mk()):
                over = {"camera.fps": f, "npu.gamma_min": 0.25}
                for pol in ("fp", "rr", "cap", "adapt", "edf"):
                    jobs.append((over, dict(policy=pol, burst=1024, seed=ci, seconds=seconds, mk=mk)))
                for dl in DROP_GRID:
                    jobs.append((over, dict(policy="drop", burst=1024, seed=ci, seconds=seconds, mk=mk,
                                            drop_frac=dl)))
    return jobs


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--seconds", type=float, default=1.0)
    ap.add_argument("--fps", default="60,120,180")
    a = ap.parse_args()
    jobs = grid(a.name, a.seeds, a.seconds, tuple(int(x) for x in a.fps.split(",")))
    RES.mkdir(exist_ok=True)
    with Pool() as p, open(RES / f"{a.name}.jsonl", "w") as f:
        for m in p.imap(one, jobs, chunksize=2):
            f.write(json.dumps(m, default=str) + "\n")
    print(a.name, len(jobs), "runs")
