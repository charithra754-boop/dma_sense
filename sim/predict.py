"""Analytic predictions (Eqs. rho, fp, rr, cap, T_c), written and committed before reading any sweep result.

Writes results/predictions.json. Uses config/params.toml only.
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dmasense as d  # noqa: E402

c = d.load_config()
bw, Bo, F = c["dram"]["peak_bw_GBps"], c["sim"]["burst_overhead_B"], c["camera"]["frame_bytes"]
Si, lam = c["imu"]["packet_bytes"], c["imu"]["rate_hz"]
t_a = Bo / bw
out = {"t_a_ns": t_a, "note": "bounds on one IMU wait (ns), queueing ignored; N = 3 masters for RR",
       "wait_bound_ns": {}, "rho": {}, "knee_fps": {}}
for B in c["camera"]["burst_sweep_B"]:
    out["wait_bound_ns"][B] = {"fp": t_a + B / bw, "rr": t_a + 2 * B / bw,
                              "cap": t_a + min(B, c["sim"]["cap_B"]) / bw,
                              "adapt": t_a + min(B, c["sim"]["split_B"]) / bw}
for B in (256, 1024):
    eta = B / (B + Bo)
    per_frame = 2 * F            # camera write + NPU read of the whole frame (gamma = 1)
    out["rho"][B] = {f: (f * per_frame + lam * Si) / (bw * 1e9 * eta) for f in c["camera"]["fps_sweep"]}
    out["knee_fps"][B] = (bw * 1e9 * eta - lam * Si) / per_frame
out["H1_slope_pred_ns_per_B"] = {"fp": 1 / bw, "rr": 2 / bw, "cap": 0.0}
Path("results").mkdir(exist_ok=True)
Path("results/predictions.json").write_text(json.dumps(out, indent=1))
print("knee_fps", {k: round(v, 1) for k, v in out["knee_fps"].items()})
print("fp bound 1KB (us)", round(out["wait_bound_ns"][1024]["fp"] / 1e3, 2))
