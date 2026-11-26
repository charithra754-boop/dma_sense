"""Run parameter grids in parallel; one JSON line per run in results/<name>.jsonl.

  burst   B_c in burst_sweep_B x {fp, rr, cap, adapt} at 60 fps     -> burst figure, H1 fit, Table pred
  fps     fps_sweep x {fp} at B_c = 1024                             -> knee figure, knee value
Usage: python -I sim/sweep.py burst|fps [--seeds 10] [--seconds 1.0]
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
    return m


def grid(name, seeds, seconds):
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
    return jobs


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--seconds", type=float, default=1.0)
    a = ap.parse_args()
    jobs = grid(a.name, a.seeds, a.seconds)
    RES.mkdir(exist_ok=True)
    with Pool() as p, open(RES / f"{a.name}.jsonl", "w") as f:
        for m in p.imap(one, jobs, chunksize=2):
            f.write(json.dumps(m, default=str) + "\n")
    print(a.name, len(jobs), "runs")
