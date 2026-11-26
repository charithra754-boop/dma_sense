# DMA-Sense

Does smart bus/DMA scheduling beat simple frame dropping or fixed larger bursts on a drone SoC, when camera DMA, IMU/telemetry packets and inference reads share one memory path, at equal bandwidth and energy?

COA course project. **Status:** paper and report text written; no simulator has run yet. Figures in `paper/` use illustrative data (`\illustrativetrue` in `paper/common.tex`).

## Hypotheses
- **H1** IMU p99 latency grows linearly with camera burst size under fixed priority and round-robin, and stays bounded under burst capping.
- **H2** Adaptive burst splitting beats frame dropping at equal budget.
- **H3** Above the saturation knee, dropping frames wins or ties.

## Layout
```
sim/       event-driven simulator: arbiters, cache + MSHRs, DRAM, power/DVFS
config/    committed parameters (source of the [X] entries in the parameter table)
traces/    per-frame change intensity (ERA/CapERA) and IMU streams; numbers only, no frames
results/   committed sweep outputs; every number and figure is generated from these
paper/     paper.tex, report.tex, common.tex, figs/, data/ (to be replaced by generated output)
slides/    presentation
```

## Build the documents
```
cd paper && latexmk -pdf -outdir=build paper.tex report.tex
```

## Related
Frame-change traces and the "fewer frames" baseline build on [airgap](https://github.com/ahadullabaig/airgap).
Dataset media (ERA, CapERA) are not redistributed.
