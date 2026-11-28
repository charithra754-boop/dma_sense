# DMA-Sense

**Smarter buses or fewer frames?** On a drone SoC where a camera DMA engine, an IMU/telemetry master and an NPU share one memory path, does smart bus and DMA scheduling beat simply dropping frames, at equal DRAM bandwidth and energy?

COA course project. A. U. Baig, D. Gera and C. G, Dept. of AI and ML, Ramaiah Institute of Technology, Bengaluru.

## Findings

Simulated flight-controller-class SoC: 16-bit SDR SDRAM at 0.2 GB/s, 16 KB cache, power-capped clock (50/67/100 MHz). Traffic from 120 ERA aerial clips (40 per camera-motion group) and a synthetic 1 kHz IMU stream with manoeuvre bursts. 5,160 simulator runs.

- **H1 holds.** IMU p99 latency grows with camera burst size: 5.9 ns per byte under fixed priority (predicted 5.0). Burst capping keeps it flat at 1.4 µs.
- **The knee comes early, and the clock moves it.** Frames start dropping at 92 fps, against 160 fps predicted from bandwidth alone. With the clock pinned the knee is at 123 fps, and the fixed-priority tail falls from 7.1 µs to 5.2 µs, onto the analytic bound.
- **H2 holds, but barely matters.** At equal budget, adaptive burst splitting cuts IMU p99 by 5 µs with no cost in frame age or energy. Against a 1 ms control deadline that is under 1%.
- **H3 holds.** Above the knee, frame dropping ties or beats every scheduler on frame age. Least-slack-first arbitration starves the IMU (p99 +1.6 ms, past its deadline).
- **No scheduler improves frame age or energy.** Burst blocking, which every scheduler targets, is at most 3% of stall time. The queue limit, which is frame dropping, decides freshness; the arbiter only decides the IMU tail.

At phone-class bandwidth (LPDDR4X, 12.8 GB/s) the bus is under 1% utilised at 60 fps and none of these effects appear; that is why the flight-controller platform was chosen (report, Sec. 3).

## Documents

| | LaTeX (master) | Google Docs |
|---|---|---|
| IEEE paper, 5 pages | `paper/paper.tex` | `docs/DMA-Sense_IEEE_paper.docx` |
| Project report, 18 pages | `paper/report.tex` | `docs/DMA-Sense_project_report.docx` |
| Slides, 10 | | `slides/DMA-Sense_slides.pptx` |

Every number in the documents is a macro in `paper/generated/numbers.tex`, generated from the committed result files; each macro names its source.

## Layout

```
config/params.toml   every parameter, committed before the sweeps; each tagged data / datasheet / assumed
sim/                 dmasense.py (simulator), validate.py, predict.py, sweep.py, analyze.py, extra_tables.py
traces/              ERA per-clip table and per-frame change intensity m_k (numbers only, no frames)
results/             one JSON line per run, validation, predictions (committed before results), summary
paper/               LaTeX sources, figures (pgfplots), generated data and tables
docs/                .docx builder and figure PNGs
slides/              slide builder and deck
```

## Reproduce

Python 3.11+, standard library only for the simulator.

```
python -I sim/predict.py            # analytic predictions
python -I sim/validate.py           # byte conservation, utilisation, blocking bounds, determinism
python -I sim/sweep.py burst        # 240 runs
python -I sim/sweep.py fps          # 100 runs
python -I sim/sweep.py policy       # 4,320 runs: 120 clips x 3 frame rates x 12 configurations
python -I sim/sweep.py ablation --seeds 5
python -I sim/sweep.py pinned --seeds 5
python -I sim/analyze.py            # paper/data/*.csv, paper/generated/*
python -I sim/extra_tables.py       # report-only tables
cd paper && latexmk -pdf -outdir=build paper.tex report.tex
```

Rebuilding the m_k traces needs the ERA videos and OpenCV: `python -P traces/era_mk.py <ERA_Dataset.zip> traces/era_mk.csv`. The Word and PowerPoint files need `pypandoc_binary` and `python-pptx`: `python docs/build_docs.py`, `python slides/build_slides.py`.

## Data and terms

ERA videos (Mou et al., 2020) are for academic use and are not redistributed; the repository holds clip names and numbers only. See `DATA.md`. The frame-change traces and the equal-budget method build on [airgap](https://github.com/ahadullabaig/airgap).
