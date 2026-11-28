"""10-slide deck for DMA-Sense, styled after the airgap deck. Numbers come from the committed results.

Usage: python slides/build_slides.py     (needs python-pptx; figures from docs/figures, built by docs/build_docs.py)
Font: Inter (built into Google Slides; PowerPoint substitutes if it is not installed).
"""
import json
import re
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt, Emu

ROOT = Path(__file__).resolve().parent.parent
FIG = ROOT / "docs" / "figures"
N = dict(re.findall(r"\\newcommand\{\\(\w+)\}\{(.*?)\}", (ROOT / "paper" / "generated" / "numbers.tex").read_text()))
N = {k: v.replace("{,}", ",") for k, v in N.items()}
S = json.loads((ROOT / "results" / "summary.json").read_text())

BG, INK, MUTED, BLUE = RGBColor(0xF7, 0xF6, 0xF2), RGBColor(0x11, 0x18, 0x27), RGBColor(0x6B, 0x72, 0x80), RGBColor(0x1D, 0x6F, 0xE0)
DARK, WHITE, LINE, PALE = RGBColor(0x11, 0x18, 0x27), RGBColor(0xFF, 0xFF, 0xFF), RGBColor(0xE3, 0xE5, 0xE8), RGBColor(0xEE, 0xF4, 0xFD)
FONT = "Inter"
TITLE = "Smarter Buses or Fewer Frames? \u00b7 Drone SoC simulator"

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
BLANK = prs.slide_layouts[6]
W = prs.slide_width


def bg(slide, color):
    f = slide.background.fill
    f.solid()
    f.fore_color.rgb = color


def text(slide, x, y, w, h, s, size=18, color=INK, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.1):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, line in enumerate(s.split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = spacing
        r = p.add_run()
        r.text = line
        r.font.size, r.font.bold, r.font.name = Pt(size), bold, FONT
        r.font.color.rgb = color
    return tb


def card(slide, x, y, w, h, head, body, fill=WHITE, head_color=INK, body_color=MUTED, border=LINE, hs=16, bs=13):
    sh = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    sh.adjustments[0] = 0.06
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    sh.line.color.rgb = border
    sh.line.width = Pt(0.75)
    sh.shadow.inherit = False
    text(slide, x + 0.22, y + 0.18, w - 0.44, 0.4, head, hs, head_color, bold=True)
    text(slide, x + 0.22, y + 0.6, w - 0.44, h - 0.75, body, bs, body_color, spacing=1.15)


def frame(slide, kicker, title, n, dark=False):
    bg(slide, DARK if dark else BG)
    text(slide, 0.6, 0.45, 12, 0.3, kicker.upper(), 11, BLUE if not dark else RGBColor(0x6C, 0xA6, 0xF5), bold=True)
    text(slide, 0.6, 0.75, 12.1, 0.9, title, 30, WHITE if dark else INK, bold=True)
    c = RGBColor(0x9C, 0xA3, 0xAF) if dark else MUTED
    text(slide, 0.6, 7.0, 10, 0.25, TITLE, 9, c)
    text(slide, 12.2, 7.0, 0.5, 0.25, str(n), 9, c, align=PP_ALIGN.RIGHT)


def stat(slide, x, y, value, label, color=BLUE, lab_color=MUTED, size=44, w=3.0):
    text(slide, x, y, w, 0.8, value, size, color, bold=True)
    text(slide, x, y + 0.85, w, 0.5, label, 12, lab_color)


def picture(slide, name, x, y, w=None, h=None):
    kw = {}
    if w:
        kw["width"] = Inches(w)
    if h:
        kw["height"] = Inches(h)
    return slide.shapes.add_picture(str(FIG / f"{name}.png"), Inches(x), Inches(y), **kw)


def table(slide, x, y, w, rows, col_w, size=12, header_fill=None, dark=False, row_h=0.36, highlight=None):
    shp = slide.shapes.add_table(len(rows), len(rows[0]), Inches(x), Inches(y), Inches(w), Inches(row_h * len(rows)))
    t = shp.table
    t.first_row = True
    for j, cw in enumerate(col_w):
        t.columns[j].width = Inches(cw)
    for i, row in enumerate(rows):
        t.rows[i].height = Inches(row_h)
        for j, val in enumerate(row):
            cell = t.cell(i, j)
            cell.fill.solid()
            if dark:
                cell.fill.fore_color.rgb = RGBColor(0x1F, 0x29, 0x37) if i == 0 else DARK
            else:
                cell.fill.fore_color.rgb = (RGBColor(0xEC, 0xEE, 0xF1) if i == 0 else
                                            (PALE if highlight is not None and i == highlight else WHITE))
            cell.margin_left = cell.margin_right = Inches(0.08)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            tf = cell.text_frame
            tf.paragraphs[0].text = ""
            r = tf.paragraphs[0].add_run()
            r.text = str(val)
            r.font.size, r.font.name = Pt(size), FONT
            r.font.bold = i == 0
            r.font.color.rgb = (WHITE if dark else INK) if i else ((RGBColor(0xD1, 0xD5, 0xDB)) if dark else MUTED)
            tf.paragraphs[0].alignment = PP_ALIGN.LEFT if j == 0 else PP_ALIGN.RIGHT
    return t


# 1 title
s = prs.slides.add_slide(BLANK)
bg(s, BG)
text(s, 0.8, 0.8, 6, 0.5, "DMA-Sense", 26, INK, bold=True)
text(s, 0.8, 1.9, 11.8, 1.0, "Smarter Buses or Fewer Frames?", 48, INK, bold=True)
text(s, 0.8, 3.15, 11.5, 0.6, "Burst sizing, arbitration and frame dropping on a drone SoC", 22, MUTED)
text(s, 0.8, 5.6, 11, 0.4, "Ahad Ulla Baig \u00b7 Divisha Gera \u00b7 Charithra G", 16, INK, bold=True)
text(s, 0.8, 6.05, 11.5, 0.4, "Computer Organisation and Architecture [course code] \u00b7 Supervised by [professor\u2019s name] \u00b7 "
     "Ramaiah Institute of Technology, Bengaluru", 12, MUTED)

# 2 question
s = prs.slides.add_slide(BLANK)
frame(s, "The question", "Three masters share one memory path.\nDoes smart scheduling beat dropping frames?", 2)
stat(s, 0.6, 2.35, "614,400 B", "per camera frame (640\u00d7640, NV12)", w=3.6)
stat(s, 4.6, 2.35, "32 B", "per IMU packet, 1 kHz", w=3.0)
stat(s, 8.0, 2.35, "0.2 GB/s", "shared 16-bit SDRAM path", w=4.0)
card(s, 0.6, 4.1, 5.9, 2.5, "The conflict",
     "A camera burst holds the bus until it finishes. A 2 KB burst delays a 32-byte IMU packet by about 10 \u00b5s, "
     "and the IMU feeds the flight-control loop.")
card(s, 6.8, 4.1, 5.9, 2.5, "The rule: equal budget",
     "Schedulers (burst capping, adaptive splitting, deadline-aware) are compared with plain frame dropping only "
     "after dropping is matched to their DRAM bandwidth and energy.", fill=PALE, border=RGBColor(0xC7, 0xDB, 0xF8))

# 3 system + hypotheses
s = prs.slides.add_slide(BLANK)
frame(s, "The system", "A flight-controller-class SoC, and three hypotheses", 3)
card(s, 0.6, 1.95, 5.6, 4.75, "Modelled SoC", "")
picture(s, "arch", 0.85, 2.65, w=5.1)
text(s, 0.85, 5.35, 5.1, 1.2, "Non-preemptive bus \u00b7 16 KB cache, 4 MSHRs \u00b7 4-bank SDR SDRAM \u00b7 governor "
     "steps 50/67/100 MHz under a 0.30 W cap", 11, MUTED)
card(s, 6.5, 1.95, 6.2, 1.45, "H1  Burst blocking", "IMU p99 grows linearly with camera burst size under fixed priority "
     "and round-robin; capping bounds it.")
card(s, 6.5, 3.6, 6.2, 1.45, "H2  Equal budget", "Adaptive burst splitting lowers IMU p99 without raising frame age, "
     "relative to frame dropping.")
card(s, 6.5, 5.25, 6.2, 1.45, "H3  When dropping wins", "Above the saturation knee, frame dropping gives equal or "
     "lower frame age than any scheduler.")

# 4 method
s = prs.slides.add_slide(BLANK)
frame(s, "The method", "Predict first, validate, then measure", 4)
stat(s, 0.6, 1.95, f"{int(N['nRuns']):,}", "simulator runs", w=2.9)
stat(s, 3.7, 1.95, N["nClips"], "ERA aerial clips, 40 per motion group", w=3.2)
stat(s, 7.2, 1.95, "4 / 4", "validation checks passed", w=2.6)
stat(s, 10.0, 1.95, "0.82\u20131.00", "fixed-priority max wait / bound", w=2.8, size=40)
card(s, 0.6, 3.75, 3.9, 2.9, "Simulator", "Event-driven, deterministic. Five arbiters, cache with MSHRs, open-row SDRAM, "
     "power-capped DVFS. Every request logs why it waited.")
card(s, 4.72, 3.75, 3.9, 2.9, "Predictions", "Blocking bounds and the knee, written from the formulas and committed "
     "before any sweep result was read.")
card(s, 8.84, 3.75, 3.9, 2.9, "Traces", "Per-frame change intensity m_k from ERA clips drives the NPU's reads. "
     "Synthetic 1 kHz IMU with 4\u00d7 bursts in manoeuvres.")

# 5 H1
s = prs.slides.add_slide(BLANK)
frame(s, "H1: burst blocking", "Latency grows with burst size, as predicted", 5)
card(s, 0.6, 1.95, 6.6, 4.75, "IMU p99 against camera burst, 60 fps", "")
picture(s, "burst", 0.9, 2.55, w=6.0)
h1 = S["H1"]
table(s, 7.5, 1.95, 5.2, [["Policy", "Slope (ns/B)", "Predicted"],
                          ["Fixed priority", f"{h1['fp']['slope_ns_per_B']:.1f}", "5.0"],
                          ["Round-robin", f"{h1['rr']['slope_ns_per_B']:.1f}", "10.0"],
                          ["Burst-capped", f"{abs(h1['cap']['slope_ns_per_B']):.1f}", "0.0"]],
      [2.2, 1.5, 1.5], size=13, row_h=0.45)
card(s, 7.5, 4.15, 5.2, 2.55, "H1 holds",
     "Fixed priority follows Eq. (2) closely (R\u00b2 = 0.99). Capping holds the tail flat at about 1.4 \u00b5s. "
     "Round-robin grows more slowly than its bound, which assumes every master has a burst waiting.",
     fill=PALE, border=RGBColor(0xC7, 0xDB, 0xF8))

# 6 clock (dark)
s = prs.slides.add_slide(BLANK)
frame(s, "The clock", "The power cap moves the knee more than any arbiter", 6, dark=True)
picture(s, "knee", 0.6, 1.85, w=6.3)
stat(s, 7.4, 1.95, f"{N['kneeFps']} fps", "knee, power-capped (frames start dropping)", color=WHITE,
     lab_color=RGBColor(0x9C, 0xA3, 0xAF), w=5.3)
stat(s, 7.4, 3.3, f"{N['kneePinnedFps']} fps", "knee with the clock pinned at 100 MHz", color=RGBColor(0x6C, 0xA6, 0xF5),
     lab_color=RGBColor(0x9C, 0xA3, 0xAF), w=5.3)
stat(s, 7.4, 4.65, f"{N['kneePredFps']} fps", "predicted from bandwidth alone", color=RGBColor(0x9C, 0xA3, 0xAF),
     lab_color=RGBColor(0x9C, 0xA3, 0xAF), w=5.3)
text(s, 7.4, 6.05, 5.3, 0.8, f"Pinned, the fixed-priority IMU p99 falls from {N['predFPPNinetyNine']} to "
     f"{N['pinFPPNinetyNine']} \u00b5s: onto the {N['predFPBound']} \u00b5s bound.", 13, WHITE)

# 7 equal budget
s = prs.slides.add_slide(BLANK)
frame(s, "H2 and H3: equal budget", "At equal budget, dropping matches or wins on freshness", 7)
r = S["policy"]["120"]


def row(lab, o, key="policy"):
    return [lab, f"{o['delta']:.1f}" if key == "policy" else "0.0", f"{o['imu_p99_us'][key][0]:.2f}",
            f"{o['age_mean_ms'][key][0]:.1f}", f"{o['drop_rate'][key][0]:.1f}", f"{o['energy_per_frame_mJ'][key][0]:.2f}"]


rows = [["Policy (120 fps, 120 clips)", "\u03b4", "IMU p99 (\u00b5s)", "Age (ms)", "Drop (%)", "E (mJ/frame)"],
        row("Frame dropping (FP, 1 KB)", r["fp"], "drop"), row("Round-robin", r["rr"]), row("Burst-capped", r["cap"]),
        row("Adaptive split", r["adapt"]), row("Age/deadline", r["edf"])]
table(s, 0.6, 1.95, 12.1, rows, [3.9, 0.9, 1.9, 1.7, 1.7, 2.0], size=13, row_h=0.42, highlight=1)
card(s, 0.6, 5.0, 3.9, 1.8, "H2 holds, barely",
     f"Adaptive splitting cuts IMU p99 by {N['dImuAdaptOneTwentyAbs']} \u00b5s at no cost. That is under 1% of the 1 ms deadline.")
card(s, 4.72, 5.0, 3.9, 1.8, "H3 holds",
     "At 180 fps dropping ties adaptive splitting and beats every other scheduler on frame age.")
card(s, 8.84, 5.0, 3.9, 1.8, "Deadline-aware fails",
     f"Above the knee late camera and NPU requests win arbitration: IMU p99 +{N['dImuEdfOneEighty']} \u00b5s, past the deadline.")
text(s, 0.6, 4.6, 12, 0.3, "Intervals: 95% paired bootstrap over clips (in the paper). \u03b4 = drop fraction matched to each policy's budget.", 10, MUTED)

# 8 stalls + energy
s = prs.slides.add_slide(BLANK)
frame(s, "Where the time goes", "Schedulers attack the smallest stall", 8)
card(s, 0.6, 1.95, 6.0, 3.9, "Stall time by cause, 120 fps", "")
picture(s, "stall", 1.1, 2.5, h=3.2)
card(s, 6.75, 1.95, 6.0, 3.9, "Energy per processed frame", "")
picture(s, "energy", 7.35, 2.5, h=3.2)
text(s, 0.6, 6.05, 12, 0.8, "Burst blocking is at most 3% of stall time; almost all of it is the camera queueing behind its own "
     "bursts and the NPU. No scheduler saves energy: smaller pieces keep the bus busy longer, and the governor lowers the clock.",
     13, INK)

# 9 lessons
s = prs.slides.add_slide(BLANK)
frame(s, "Lessons for small SoCs", "What a designer should take away", 9)
L = [("Size the queue, not the arbiter", "Queue limit 1 \u2192 about 10 ms frame age; 4 \u2192 94 ms. No arbiter moved age that much."),
     ("Account for the power cap", "The governor cost about 30 fps of headroom and pushed the tail past the bound."),
     ("Split adaptively, if anything", "It removes most blocking at no cost in age or energy; capping adds both."),
     ("No least-slack near overload", "Late frames acquire less slack than fresh IMU packets, and the IMU misses its deadline."),
     ("Test against the simple option", "Matched for bandwidth and energy, frame dropping was the better default."),
     ("Check the platform first", "At 12.8 GB/s the bus was under 1% busy: none of this would have shown up.")]
for i, (h, b) in enumerate(L):
    x = 0.6 + (i % 3) * 4.12
    y = 1.95 + (i // 3) * 2.45
    card(s, x, y, 3.9, 2.2, h, b, hs=15, bs=12.5)

# 10 questions
s = prs.slides.add_slide(BLANK)
bg(s, BLUE)
text(s, 0.8, 2.6, 11, 1.2, "Questions?", 54, WHITE, bold=True)
text(s, 0.8, 3.8, 11, 0.5, "github.com/charithra754-boop/dma_sense", 18, WHITE)
text(s, 0.8, 6.55, 11.8, 0.5, "Paper, report and slides from one repository \u00b7 every number generated from committed "
     "simulator results \u00b7 limitations: simulator, not silicon; assumed power coefficients; synthetic IMU stream", 10, WHITE)

out = ROOT / "slides" / "DMA-Sense_slides.pptx"
prs.save(out)
print("wrote", out, len(prs.slides), "slides")
