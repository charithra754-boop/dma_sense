"""DMA-Sense: event-driven simulator of one shared memory path on a drone SoC.

Masters: camera DMA (cam), IMU/telemetry (imu), NPU reads (npu), optional background (bg).
One non-preemptive bus: a granted piece of a request holds the bus for its whole service.
Behind the bus: a shared set-associative LRU cache (write-back or write-through) and a DRAM
with per-bank open rows. MSHRs bound how many read misses of one burst overlap their latency.
A power model (P = Ps + C V^2 f activity + e_b * DRAM rate) drives a capped DVFS governor.

Every served piece logs where its delay came from:
  burst  residual of the burst already holding the bus when the request arrived
  arb    bursts of other masters granted ahead of it after it arrived
  self   bursts of its own master queued ahead of it (e.g. the NPU's earlier reads)
  cache  own service time beyond the bus time, caused by MSHR-limited miss latency
  dram   own service time beyond the bus time, caused by DRAM bandwidth and row activation

Units: time in ns, bandwidth in bytes/ns (= GB/s), sizes in bytes.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import math
import random
import tomllib
from collections import OrderedDict, deque
from copy import deepcopy
from pathlib import Path

CAUSES = ("arb", "self", "burst", "cache", "dram")
MASTERS = ("imu", "npu", "cam", "bg")          # fixed-priority order, highest first
RR_ORDER = ("imu", "cam", "npu", "bg")
POLICIES = ("drop", "fp", "rr", "cap", "adapt", "edf")
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "params.toml"

CAM_BASE, NPU_W_BASE, IMU_BASE, BG_BASE = 0x1000_0000, 0x4000_0000, 0x5000_0000, 0x6000_0000
IMU_RING = 4096


def load_config(path=DEFAULT_CONFIG, **overrides):
    """Load params.toml; overrides use 'section.key' names, e.g. {'camera.fps': 60}."""
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg = deepcopy(cfg)
    for k, v in overrides.items():
        sec, key = k.split(".", 1)
        cfg.setdefault(sec, {})[key] = v
    return cfg


class Req:
    __slots__ = ("master", "left", "nbytes", "arr", "deadline", "write", "addr", "tag",
                 "first", "last_end", "cause", "own0", "tot0")

    def __init__(self, master, nbytes, arr, deadline, write, addr, tag=None):
        self.master, self.nbytes, self.left = master, nbytes, nbytes
        self.arr, self.deadline, self.write, self.addr, self.tag = arr, deadline, write, addr, tag
        self.first = True          # no piece granted yet
        self.last_end = arr        # end of its previous piece (or arrival)
        self.cause = dict.fromkeys(CAUSES, 0.0)
        self.own0 = self.tot0 = 0.0  # busy-time snapshots at the start of its current wait


class Cache:
    def __init__(self, size_kb, ways, line, write_policy, enabled=True):
        self.line, self.ways, self.wb = line, ways, write_policy == "wb"
        self.nsets = max(1, size_kb * 1024 // (line * ways))
        self.sets = [OrderedDict() for _ in range(self.nsets)]
        self.enabled = enabled and size_kb > 0
        self.hits = self.misses = 0

    def access(self, ln, write, full_line):
        """Return (dram_reads, dram_writes) caused by touching line number ln."""
        if not self.enabled:
            return (0, 1) if write else (1, 0)
        s = self.sets[ln % self.nsets]
        if ln in s:
            self.hits += 1
            s.move_to_end(ln)
            if write:
                if self.wb:
                    s[ln] = True
                else:
                    return 0, 1
            return 0, 0
        self.misses += 1
        if write and not self.wb:                      # write-through, no write-allocate
            return 0, 1
        reads = 0 if (write and full_line) else 1      # full-line writes need no fetch
        wbk = 0
        if len(s) >= self.ways:
            _, dirty = s.popitem(last=False)
            wbk = 1 if dirty else 0
        s[ln] = bool(write)
        return reads, wbk


class Sim:
    def __init__(self, cfg, policy="fp", burst=1024, seed=0, seconds=1.0, mk=None,
                 drop_frac=0.0, imu_trace=None):
        assert policy in POLICIES, policy
        self.cfg, self.policy, self.B, self.seed = cfg, policy, int(burst), seed
        self.rng = random.Random(seed)
        self.T = seconds * 1e9
        d, c, n, i, ca, q, p = (cfg[s] for s in ("dram", "camera", "npu", "imu", "cache",
                                                  "queue", "power"))
        self.sim = cfg.get("sim", {})
        self.ideal = bool(self.sim.get("ideal", False))
        self.on = set(self.sim.get("masters", ["cam", "imu", "npu", "bg"]))   # no cache, no row/latency effects
        self.bw = d["peak_bw_GBps"]
        self.B_o = self.sim.get("burst_overhead_B", 64)
        self.t_line = cfg["cache"]["line_B"] / self.bw
        self.t_act = d["row_miss_ns"] - d["row_hit_ns"]
        self.t_cas = d["row_hit_ns"]
        self.row_bytes, self.banks = d["row_bytes"], d.get("banks", 8)
        self.open_row = [-1] * self.banks
        self.cache = Cache(ca["size_KB"], ca["ways"], ca["line_B"], ca["write_policy"],
                           enabled=not self.ideal)
        self.line = ca["line_B"]
        self.mshrs = ca["mshrs"]
        self.cam_cached = bool(self.sim.get("cam_through_cache", True))
        # camera
        self.fps = c["fps"] if "fps" in c else c["native_fps"]
        self.F = int(c["frame_bytes"])
        self.readout = c.get("readout_frac", 0.9) / self.fps * 1e9
        self.Q = q["limit_frames"]
        self.mk = mk                       # per-frame change intensity in [0,1], or None
        self.drop_frac = drop_frac if policy == "drop" else 0.0
        self.drop_select = self.sim.get("drop_select", "uniform")
        # npu
        self.npu_gamma_min = n.get("gamma_min", 1.0)
        self.npu_w = n.get("weight_bytes_per_frame", 0)
        self.npu_burst = n.get("burst_B", 256)
        self.npu_comp = n.get("compute_ms", 4.0) * 1e6
        self.npu_out = n.get("outstanding", 4)
        # imu
        self.imu_rate = i["rate_hz"]
        self.imu_S = i["packet_bytes"]
        self.imu_deadline = i["deadline_us"] * 1e3
        self.imu_jitter = i.get("jitter_frac", 0.02)
        self.man_every = i.get("manoeuvre_every_s", 2.0) * 1e9
        self.man_len = i.get("manoeuvre_len_ms", 200) * 1e6
        self.man_mult = i.get("manoeuvre_rate_mult", 4)
        self.imu_trace = imu_trace
        # background
        self.bg_rate = self.sim.get("bg_MBps", 0.0) * 1e-3   # bytes/ns
        self.bg_B = self.sim.get("bg_burst_B", 256)
        # policy knobs
        self.B_cap = self.sim.get("cap_B", 256)
        self.B_s = self.sim.get("split_B", 128)
        # power / DVFS
        self.Ps, self.cap_W = p["static_W"], p["cap_W"]
        self.steps = sorted(p["dvfs_steps_MHz"])
        self.fmax = p["f_max_MHz"]
        self.vmin, self.vmax = p.get("v_min", 0.6), p.get("v_max", 1.1)
        self.c_nF = p.get("c_eff_nF", 0.52)
        self.e_b = p["e_bit_pJ"] * 8e-12              # J per DRAM byte
        self.win = p.get("window_us", 100) * 1e3
        self.dvfs = bool(p.get("dvfs", True))
        self.step_i = len(self.steps) - 1
        self.win_start, self.win_busy, self.win_dram = 0.0, 0.0, 0
        self.energy = 0.0
        self.f_time = 0.0                              # integral of f dt (for mean clock)
        # state
        self.q = {m: deque() for m in MASTERS}
        self.ev = []
        self.seq = 0
        self.rr_ptr = 0
        self.t = 0.0
        self.last_start = self.last_end = 0.0
        self.npu_frames = deque()
        self.npu_busy = False
        self.npu_pending = deque()      # requests of the current NPU frame not yet issued
        self.npu_inflight = 0
        self.npu_frame = None
        self.next_imu = None
        # logs
        self.bytes_req = dict.fromkeys(MASTERS, 0)
        self.bytes_srv = dict.fromkeys(MASTERS, 0)
        self.stall = {m: dict.fromkeys(CAUSES, 0.0) for m in MASTERS}
        self.lat = {m: [] for m in MASTERS}
        self.busy = 0.0
        self.busy_by = dict.fromkeys(MASTERS, 0.0)
        self.last_master = None
        self.dram_bytes = 0
        self.frames = []                # dicts per captured frame
        self.max_wait = {m: 0.0 for m in MASTERS}

    # ---------------- events ----------------
    def push(self, t, kind, payload=None):
        self.seq += 1
        heapq.heappush(self.ev, (t, self.seq, kind, payload))

    def setup(self):
        self.push(0.0, "frame")
        if "imu" not in self.on:
            pass
        elif self.imu_trace is not None:
            self.imu_trace = sorted(x for x in self.imu_trace if x < self.T)
            self.imu_i = 0
            self.next_imu = self.imu_trace[0] if self.imu_trace else None
            for ti in self.imu_trace:
                if ti < self.T:
                    self.push(ti, "imu", False)
        else:
            self.next_imu = self.rng.uniform(0, 1e9 / self.imu_rate)
            self.push(self.next_imu, "imu", True)
        self.manoeuvres = []
        tm = self.rng.expovariate(1 / self.man_every)
        while tm < self.T:
            self.manoeuvres.append((tm, tm + self.man_len))
            tm += self.man_len + self.rng.expovariate(1 / self.man_every)
        if self.bg_rate > 0 and "bg" in self.on:
            self.push(self.rng.expovariate(self.bg_rate / self.bg_B), "bg")

    def in_manoeuvre(self, t):
        return any(a <= t < b for a, b in self.manoeuvres)

    def enqueue(self, r):
        self.bytes_req[r.master] += r.nbytes
        # burst blocking: the request arrived while the last-granted piece held the bus
        if self.last_end > r.arr > self.last_start:
            res = self.last_end - r.arr
            r.cause["self" if self.last_master == r.master else "burst"] += res
            r.last_end = self.last_end
        r.own0, r.tot0 = self.busy_by[r.master], self.busy
        self.q[r.master].append(r)

    def handle(self, t, kind, pl):
        if kind == "frame":
            self.frame_start(t)
        elif kind == "camb":
            k, j, n, nb = pl
            fr = self.frames[k]
            dl = fr["t_cap"] + 1e9 / self.fps
            self.enqueue(Req("cam", nb, t, dl, True, fr["buf"] + j * self.B, tag=k))
            if j + 1 < n:
                nxt = fr["t0"] + (j + 2) / n * self.readout
                last = self.F - (j + 1) * self.B
                self.push(nxt, "camb", (k, j + 1, n, min(self.B, last)))
        elif kind == "imu":
            addr = IMU_BASE + (self.seq * self.imu_S) % IMU_RING
            self.enqueue(Req("imu", self.imu_S, t, t + self.imu_deadline, True, addr))
            if pl:  # synthetic: schedule next
                rate = self.imu_rate * (self.man_mult if self.in_manoeuvre(t) else 1)
                per = 1e9 / rate
                self.next_imu = t + per * (1 + self.rng.uniform(-self.imu_jitter, self.imu_jitter))
                self.push(self.next_imu, "imu", True)
            else:
                self.imu_i += 1
                self.next_imu = self.imu_trace[self.imu_i] if self.imu_i < len(self.imu_trace) else None
        elif kind == "bg":
            addr = BG_BASE + self.rng.randrange(0, 64 << 20) // 32 * 32
            self.enqueue(Req("bg", self.bg_B, t, math.inf, self.rng.random() < 0.5, addr))
            self.push(t + self.rng.expovariate(self.bg_rate / self.bg_B), "bg")
        elif kind == "npu_done":
            fr = self.frames[pl]
            fr["t_done"] = t
            self.npu_busy = False
            self.npu_frame = None
            self.npu_next(t)

    def drop_this(self, k):
        if self.drop_frac <= 0:
            return False
        if self.drop_select == "low_change" and self.mk is not None:
            thr = self._mk_thr
            return self.mk[k % len(self.mk)] < thr
        return math.floor((k + 1) * self.drop_frac) > math.floor(k * self.drop_frac)

    def frame_start(self, t):
        k = len(self.frames)
        if k == 0 and self.drop_select == "low_change" and self.mk is not None:
            self._mk_thr = sorted(self.mk)[min(len(self.mk) - 1, int(self.drop_frac * len(self.mk)))]
        nbuf = self.Q + 2
        fr = {"k": k, "t0": t, "t_cap": t + self.readout, "buf": CAM_BASE + (k % nbuf) * (1 << 21),
              "dropped": None, "t_written": None, "t_done": None,
              "mk": self.mk[k % len(self.mk)] if self.mk is not None else 1.0}
        writing = self.cam_writing()
        self.frames.append(fr)
        if self.drop_this(k):
            fr["dropped"] = "policy"
        elif len(self.npu_frames) + writing >= self.Q:
            fr["dropped"] = "queue"
        else:
            n = math.ceil(self.F / self.B)
            fr["n_bursts"], fr["bursts_done"] = n, 0
            self.push(t + self.readout / n, "camb", (k, 0, n, min(self.B, self.F)))
        nxt = t + 1e9 / self.fps
        if nxt < self.T:
            self.push(nxt, "frame")

    def cam_writing(self):
        return sum(1 for f in self.frames[-3:] if f["dropped"] is None and f["t_written"] is None)

    # ---------------- NPU ----------------
    def npu_next(self, t):
        if self.npu_busy or not self.npu_frames:
            return
        k = self.npu_frames.popleft()
        fr = self.frames[k]
        self.npu_busy, self.npu_frame = True, k
        gamma = self.npu_gamma_min + (1 - self.npu_gamma_min) * fr["mk"]
        nin = int(self.F * gamma) // 32 * 32
        reqs = []
        for off in range(0, self.npu_w, self.npu_burst):
            reqs.append((NPU_W_BASE + off, min(self.npu_burst, self.npu_w - off)))
        for off in range(0, nin, self.npu_burst):
            reqs.append((fr["buf"] + off, min(self.npu_burst, nin - off)))
        self.npu_pending = deque(reqs)
        self.npu_inflight = 0
        self.npu_issue(t)

    def npu_issue(self, t):
        k = self.npu_frame
        dl = self.frames[k]["t_cap"] + 2e9 / self.fps
        while self.npu_pending and self.npu_inflight < self.npu_out:
            addr, nb = self.npu_pending.popleft()
            self.npu_inflight += 1
            self.enqueue(Req("npu", nb, t, dl, False, addr, tag=k))
        if not self.npu_pending and self.npu_inflight == 0:
            self.push(t + self.npu_comp, "npu_done", k)

    # ---------------- arbitration ----------------
    def pick(self, t):
        heads = [m for m in MASTERS if self.q[m]]
        if not heads:
            return None
        pol = self.policy
        if pol == "rr":
            for i in range(len(RR_ORDER)):
                m = RR_ORDER[(self.rr_ptr + i) % len(RR_ORDER)]
                if self.q[m]:
                    self.rr_ptr = (RR_ORDER.index(m) + 1) % len(RR_ORDER)
                    return m
        if pol == "edf":
            return min(heads, key=lambda m: self.q[m][0].deadline - t - self.q[m][0].left / self.bw)
        return heads[0]          # fp, drop, cap, adapt: fixed priority imu > npu > cam > bg

    def piece_size(self, r, t):
        if self.policy == "cap":
            return min(r.left, self.B_cap)
        if self.policy == "adapt" and r.master == "cam":
            if self.q["imu"] or self.imu_imminent(t, r.left):
                return min(r.left, self.B_s)
        return r.left

    def imu_imminent(self, t, nbytes):
        """An IMU packet is due before a full burst would finish (the IMU timer is known)."""
        return self.next_imu is not None and self.next_imu <= t + (nbytes + self.B_o) / self.bw

    # ---------------- service ----------------
    def service(self, r, nb, s):
        """Service time of a piece of nb bytes at clock fraction s, and its excess split."""
        bw = self.bw * s
        t_bus = (nb + self.B_o) / bw
        if self.ideal:
            self.dram_bytes += nb
            return t_bus, 0.0, 0.0
        L = self.line
        start = r.addr + (r.nbytes - r.left)
        first, last = start // L, (start + nb - 1) // L
        reads = writes = acts = rmiss = 0
        use_cache = r.master != "cam" or self.cam_cached
        for ln in range(first, last + 1):
            full = (ln * L >= start) and ((ln + 1) * L <= start + nb)
            if use_cache:
                rd, wr = self.cache.access(ln, r.write, full)
            else:
                rd, wr = (0, 1) if r.write else (1, 0)
            if rd or wr:
                row = ln * L // self.row_bytes
                b = row % self.banks
                if self.open_row[b] != row:
                    self.open_row[b] = row
                    acts += 1
            reads += rd
            writes += wr
            if rd and not r.write:
                rmiss += 1
        ops = reads + writes
        self.dram_bytes += ops * L
        t_dram = (ops * self.t_line + acts * self.t_act) / s
        t_lat = math.ceil(rmiss / self.mshrs) * self.t_cas / s if rmiss else 0.0
        own = t_dram + t_lat
        if own <= t_bus:
            return t_bus, 0.0, 0.0
        ex = own - t_bus
        return own, ex * t_lat / own, ex * t_dram / own

    # ---------------- power ----------------
    def clock_frac(self):
        return self.steps[self.step_i] / self.fmax

    def power(self, busy_frac, dram_Bps, f_mhz):
        lo, hi = self.steps[0], self.steps[-1]
        v = self.vmin + (self.vmax - self.vmin) * (f_mhz - lo) / max(1, hi - lo)
        return self.Ps + self.c_nF * 1e-9 * v * v * f_mhz * 1e6 * (0.1 + 0.9 * busy_frac) \
            + self.e_b * dram_Bps

    def close_windows(self, t):
        while t >= self.win_start + self.win:
            f = self.steps[self.step_i]
            P = self.power(min(1.0, self.win_busy / self.win), self.win_dram / self.win * 1e9, f)
            self.energy += P * self.win * 1e-9
            self.f_time += f * self.win
            if self.dvfs:
                if P > self.cap_W and self.step_i > 0:
                    self.step_i -= 1
                elif self.step_i < len(self.steps) - 1:
                    fu = self.steps[self.step_i + 1]
                    if self.power(min(1.0, self.win_busy / self.win),
                                  self.win_dram / self.win * 1e9, fu) <= self.cap_W:
                        self.step_i += 1
            self.win_start += self.win
            self.win_busy, self.win_dram = 0.0, 0

    # ---------------- main loop ----------------
    def run(self):
        self.setup()
        t = 0.0
        drain_end = self.T + 0.2e9
        while True:
            while self.ev and self.ev[0][0] <= t:
                te, _, kind, pl = heapq.heappop(self.ev)
                self.handle(te, kind, pl)
            m = self.pick(t)
            if m is None:
                if not self.ev or self.ev[0][0] > drain_end:
                    break
                t = self.ev[0][0]
                self.close_windows(t)
                continue
            r = self.q[m][0]
            nb = self.piece_size(r, t)
            wait = t - r.last_end
            if wait > 0:
                own = self.busy_by[m] - r.own0
                r.cause["self"] += own
                r.cause["arb"] += max(0.0, wait - own)
            dram0 = self.dram_bytes
            svc, ex_cache, ex_dram = self.service(r, nb, self.clock_frac())
            r.cause["cache"] += ex_cache
            r.cause["dram"] += ex_dram
            self.last_start, self.last_end = t, t + svc
            self.busy += svc
            self.busy_by[m] += svc
            self.last_master = m
            self.win_busy += svc
            self.win_dram += self.dram_bytes - dram0
            r.left -= nb
            r.first = False
            t += svc
            r.last_end = t
            r.own0, r.tot0 = self.busy_by[m], self.busy
            self.bytes_srv[m] += nb
            if r.left == 0:
                self.q[m].popleft()
                self.complete(r, t)
            self.close_windows(t)
        self.t_end = t
        self.close_windows(max(t, self.T))
        return self.metrics()

    def complete(self, r, t):
        m = r.master
        lat = t - r.arr
        self.lat[m].append(lat)
        for c in CAUSES:
            self.stall[m][c] += r.cause[c]
        if m == "cam":
            fr = self.frames[r.tag]
            fr["bursts_done"] += 1
            if fr["bursts_done"] == fr["n_bursts"]:
                fr["t_written"] = t
                if "npu" in self.on:
                    self.npu_frames.append(r.tag)
                    self.npu_next(t)
                else:
                    fr["t_done"] = t
        elif m == "npu":
            self.npu_inflight -= 1
            if r.tag == self.npu_frame:
                self.npu_issue(t)

    # ---------------- metrics ----------------
    def metrics(self):
        def pct(xs, p):
            if not xs:
                return float("nan")
            xs = sorted(xs)
            return xs[min(len(xs) - 1, int(math.ceil(p / 100 * len(xs))) - 1)]

        frames = [f for f in self.frames if f["t0"] < self.T]
        done = [f for f in frames if f["t_done"] is not None]
        ages = [(f["t_done"] - f["t_cap"]) / 1e6 for f in done]
        imu = [x / 1e3 for x in self.lat["imu"]]
        span = max(self.T, self.t_end)
        acc = self.cache.hits + self.cache.misses
        return {
            "policy": self.policy, "burst_B": self.B, "fps": self.fps, "seed": self.seed,
            "drop_frac_target": self.drop_frac,
            "imu_n": len(imu), "imu_p50_us": pct(imu, 50), "imu_p99_us": pct(imu, 99),
            "imu_max_us": max(imu) if imu else float("nan"),
            "imu_deadline_miss": sum(1 for x in imu if x * 1e3 > self.imu_deadline),
            "frames": len(frames), "frames_done": len(done),
            "drop_rate": 1 - len(done) / len(frames) if frames else float("nan"),
            "drops_policy": sum(1 for f in frames if f["dropped"] == "policy"),
            "drops_queue": sum(1 for f in frames if f["dropped"] == "queue"),
            "age_mean_ms": sum(ages) / len(ages) if ages else float("nan"),
            "age_p95_ms": pct(ages, 95),
            "util": self.busy / span,
            "dram_GBps": self.dram_bytes / span,
            "cache_miss_rate": self.cache.misses / acc if acc else float("nan"),
            "energy_J": self.energy,
            "energy_per_frame_mJ": self.energy / len(done) * 1e3 if done else float("nan"),
            "mean_f_MHz": self.f_time / (self.win_start or 1),
            "stall_ns": self.stall,
            "bytes_req": self.bytes_req, "bytes_srv": self.bytes_srv,
            "max_imu_wait_ns": max(self.lat["imu"]) if self.lat["imu"] else 0.0,
        }


def run(cfg=None, **kw):
    cfg = cfg or load_config()
    return Sim(cfg, **kw).run()


def digest(metrics):
    return hashlib.sha256(json.dumps(metrics, sort_keys=True, default=str).encode()).hexdigest()
