"""Per-frame change intensity m_k for a seeded, stratified ERA sample. Numbers only.

m_k = share of 16x16 grey blocks whose mean |frame_k - frame_{k-1}| exceeds THR (airgap's
change-map convention); mad_k = whole-frame mean absolute difference (0-255).
Sample: PER_GROUP clips per motion group (hover / moving / unreliable), random.Random(SEED),
both splits, from traces/era_clips.csv. Only the sampled mp4s are extracted from the zip.

Usage: python -I traces/era_mk.py ~/airgap/data/era/ERA_Dataset.zip traces/era_mk.csv
"""
import csv
import random
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import cv2
import numpy as np

PER_GROUP, SEED, THR, BLOCK = 40, 0, 8.0, 16
HERE = Path(__file__).resolve().parent


def sample():
    rows = list(csv.DictReader(open(HERE / "era_clips.csv")))
    rng = random.Random(SEED)
    out = []
    for g in ("hover", "moving", "unreliable"):
        pool = sorted((r for r in rows if r["group"] == g and r["pts_broken"] == "False"),
                      key=lambda r: (r["split"], r["cls"], r["id"]))
        out += rng.sample(pool, PER_GROUP)
    return out


def mk_series(path):
    cap = cv2.VideoCapture(str(path))
    prev, res = None, []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev is not None:
            d = np.abs(g - prev)
            h, w = (d.shape[0] // BLOCK) * BLOCK, (d.shape[1] // BLOCK) * BLOCK
            blk = d[:h, :w].reshape(h // BLOCK, BLOCK, w // BLOCK, BLOCK).mean(axis=(1, 3))
            res.append((float((blk > THR).mean()), float(d.mean())))
        prev = g
    return res


def main(zpath, dst):
    picks = sample()
    z = zipfile.ZipFile(zpath)
    names = {Path(n).name.replace(" ", ""): n for n in z.namelist() if n.endswith(".mp4")}
    with open(dst, "w", newline="") as f, tempfile.TemporaryDirectory() as tmp:
        w = csv.writer(f)
        w.writerow(["split", "cls", "id", "group", "k", "mk", "mad"])
        for i, r in enumerate(picks):
            p = Path(z.extract(names[r["id"]], tmp))
            for k, (mk, mad) in enumerate(mk_series(p), start=1):
                w.writerow([r["split"], r["cls"], r["id"], r["group"], k, f"{mk:.5f}", f"{mad:.3f}"])
            p.unlink()
            print(f"{i + 1}/{len(picks)} {r['id']}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
