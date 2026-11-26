"""Validation before trusting any number (report Sec. Validation). Writes results/validation.json.

V1 byte conservation   every master: bytes requested == bytes served, all policies
V2 single master       camera only, ideal memory: busy time == frames * ceil(F/B) * (B + B_o) / BW
                       (paper T_c) and utilisation == D_c / (BW * eta(B)) up to the last-burst remainder
V3 blocking bounds     ideal memory: max IMU wait <= t_a + B/BW (FP, cap with B_cap) and
                       <= (N-1)(B + B_o)/BW (RR, N = 3); report how close the max gets (tightness)
V4 determinism         same seed -> identical metrics digest; different seed -> different
Usage: python -I sim/validate.py
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dmasense as d  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "results" / "validation.json"
SECONDS = 0.25


def cfg(**o):
    return d.load_config(**o)


def main():
    base = cfg()
    tol_u = base["validation"]["single_master_tol_pct"]
    tol_b = base["validation"]["blocking_bound_tol_pct"]
    bw, B_o = base["dram"]["peak_bw_GBps"], base["sim"]["burst_overhead_B"]
    F = base["camera"]["frame_bytes"]
    out, ok = {}, True

    # V1
    v1 = []
    for pol in d.POLICIES:
        m = d.run(cfg(**{"sim.bg_MBps": 20.0}), policy=pol, burst=512, seconds=SECONDS,
                  drop_frac=0.25 if pol == "drop" else 0.0)
        good = m["bytes_req"] == m["bytes_srv"]
        ok &= good
        v1.append({"policy": pol, "req": m["bytes_req"], "srv": m["bytes_srv"], "pass": good})
    out["V1_conservation"] = v1

    # V2
    v2 = []
    for B in base["camera"]["burst_sweep_B"]:
        c = cfg(**{"sim.ideal": True, "sim.masters": ["cam"], "power.dvfs": False})
        s = d.Sim(c, policy="fp", burst=B, seconds=SECONDS)
        m = s.run()
        n = m["frames_done"]
        busy_pred = n * math.ceil(F / B) * (B + B_o) / bw
        rho_pred = c["camera"]["fps"] * F / (bw * 1e9 * B / (B + B_o))
        err_busy = abs(s.busy - busy_pred) / busy_pred * 100
        err_rho = abs(m["util"] - rho_pred) / rho_pred * 100
        good = err_busy < 1e-6 and err_rho < tol_u
        ok &= good
        v2.append({"burst": B, "busy_ns": s.busy, "busy_pred_ns": busy_pred, "err_busy_pct": err_busy,
                   "util": m["util"], "rho_pred": rho_pred, "err_rho_pct": err_rho, "pass": good})
    out["V2_single_master"] = v2

    # V3
    v3 = []
    for B in base["camera"]["burst_sweep_B"]:
        for pol, masters, n in (("fp", ["cam", "imu"], 2), ("cap", ["cam", "imu"], 2),
                                ("rr", ["cam", "imu", "npu"], 3)):
            c = cfg(**{"sim.ideal": True, "sim.masters": masters, "power.dvfs": False,
                       "camera.fps": 30, "npu.compute_ms": 0.5, "npu.burst_B": B})
            s = d.Sim(c, policy=pol, burst=B, seconds=SECONDS)
            m = s.run()
            own = (base["imu"]["packet_bytes"] + B_o) / bw
            wait = m["max_imu_wait_ns"] - own
            Bm = min(B, c["sim"]["cap_B"]) if pol == "cap" else B
            bound = (n - 1) * (Bm + B_o) / bw
            good = wait <= bound * (1 + tol_b / 100)
            ok &= good
            v3.append({"policy": pol, "burst": B, "N": n, "max_wait_ns": wait, "bound_ns": bound,
                       "tightness": wait / bound, "pass": good})
    out["V3_blocking_bounds"] = v3

    # V4
    a = d.digest(d.run(policy="adapt", burst=1024, seconds=SECONDS, seed=7))
    b = d.digest(d.run(policy="adapt", burst=1024, seconds=SECONDS, seed=7))
    c2 = d.digest(d.run(policy="adapt", burst=1024, seconds=SECONDS, seed=8))
    good = a == b and a != c2
    ok &= good
    out["V4_determinism"] = {"seed7_a": a, "seed7_b": b, "seed8": c2, "pass": good}

    out["all_pass"] = ok
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, default=str))
    for k, v in out.items():
        if isinstance(v, list):
            print(k, "PASS" if all(x["pass"] for x in v) else "FAIL")
            for x in v:
                if not x["pass"]:
                    print("   ", x)
        elif isinstance(v, dict):
            print(k, "PASS" if v["pass"] else "FAIL")
    print("ALL", "PASS" if ok else "FAIL")
    if "V3_blocking_bounds" in out:
        print("V3 tightness:", [(x["policy"], x["burst"], round(x["tightness"], 2)) for x in v3])
    print("V2 rho err %:", [(x["burst"], round(x["err_rho_pct"], 3)) for x in v2])


if __name__ == "__main__":
    main()
