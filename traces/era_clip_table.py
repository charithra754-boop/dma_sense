"""Per-clip ERA table for DMA-Sense: motion group and change level, numbers only.

Source: airgap results/era/scan.json (one row per ERA clip, both splits).
Motion groups use airgap's rule (bench/era_report.py: bucket, airgap commit 8ee8998):
  unreliable  if no motion estimate or reliable_frac < 0.5
  hover       if median camera motion <= 0.012 frame widths/s
  moving      otherwise
No frames or captions are read or written (ERA terms: academic use, no redistribution).

Usage: python -I traces/era_clip_table.py ~/airgap/results/era/scan.json traces/era_clips.csv
"""
import csv
import json
import sys

HOVER_MAX_FW_PER_S = 0.012
HOVER_MIN_RELIABLE = 0.5


def group(m):
    if m["median_fw_per_s"] is None or m["reliable_frac"] < HOVER_MIN_RELIABLE:
        return "unreliable"
    return "hover" if m["median_fw_per_s"] <= HOVER_MAX_FW_PER_S else "moving"


def main(src, dst):
    clips = json.load(open(src))["clips"]
    cols = ["split", "cls", "id", "frames", "fps", "width", "height", "pts_broken",
            "mad1_median", "motion_median_fw_s", "motion_p90_fw_s", "motion_reliable_frac",
            "shot_events", "group"]
    with open(dst, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for c in clips:
            m = c["motion"]
            w.writerow([c["split"], c["cls"], c["id"], c["frames"], c["fps"], c["width"],
                        c["height"], c["pts_broken"], c["mad1_median"], m["median_fw_per_s"],
                        m["p90_fw_per_s"], m["reliable_frac"], len(c["shot"]["events"]),
                        group(m)])
    print(f"{len(clips)} clips -> {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
