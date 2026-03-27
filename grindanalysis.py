#!/usr/bin/env python3
"""
grindanalysis.py

Usage:
    python grindanalysis.py path/to/file.json

Reads a JSON file containing a top-level "weights" array and a top-level
"time_motor_stop". Produces a matplotlib figure showing:
 - weights vs measurement index (points color-coded by "phase")
 - a secondary x-axis showing time in seconds (step 0.1 s)
 - vertical line at motor stop time (label at bottom)
 - linear regression computed only for phase == "prediction"
 - Pearson correlation coefficient (time vs weight) for prediction phase

Requirements:
 - Python 3.8+
 - numpy
 - matplotlib
"""

import argparse
import json
import math
from collections import defaultdict

import numpy as np
import matplotlib.pyplot as plt


def parse_args():
    p = argparse.ArgumentParser(description="Plot weights (colored by phase) and regression.")
    p.add_argument("json_file", help="Path to JSON file containing 'weights' array")
    p.add_argument("--save", "-s", help="Optional: path to save figure (e.g. output.png)")
    return p.parse_args()


def load_data(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "weights" not in data:
        raise KeyError("JSON does not contain 'weights' key.")
    weights = data["weights"]
    motor_stop = data.get("time_motor_stop", None)
    return weights, motor_stop, data


def prepare_arrays(weights_list):
    # Ensure sorted by time
    weights_sorted = sorted(weights_list, key=lambda d: d["time"])
    times = np.array([float(d["time"]) for d in weights_sorted])
    vals = np.array([float(d["weight"]) for d in weights_sorted])
    phases = [d.get("phase", "unknown") for d in weights_sorted]

    # Convert times to seconds relative to the first measurement
    t0 = times[0] if times.size > 0 else 0.0
    times_sec = times - t0

    indices = np.arange(len(vals), dtype=float)  # primary x-axis: measurement index

    return indices, times_sec, vals, phases, t0


def compute_prediction_regression(times_sec, vals, phases):
    # Filter to prediction phase
    mask = np.array([p == "prediction" for p in phases])
    if mask.sum() < 2:
        return None  # not enough points

    t_pred = times_sec[mask]
    y_pred = vals[mask]

    # Linear regression (least squares): fit y = m * t + b
    m, b = np.polyfit(t_pred, y_pred, deg=1)

    # Pearson correlation coefficient between time and weight for prediction phase
    if t_pred.size >= 2:
        r = np.corrcoef(t_pred, y_pred)[0, 1]
    else:
        r = float("nan")

    return {
        "slope": float(m),
        "intercept": float(b),
        "r": float(r),
        "t_pred": t_pred,
        "y_pred": y_pred,
        "mask": mask
    }


def make_phase_colors(phases_unique):
    # Simple mapping: choose some distinct colors
    default_palette = {
        "prediction": "#1f77b4",  # blue
        "cooldown": "#ff7f0e",    # orange
        "unknown": "#2ca02c",     # green
    }
    colors = {}
    for i, p in enumerate(phases_unique):
        if p in default_palette:
            colors[p] = default_palette[p]
        else:
            # generate other colors if needed using matplotlib's tab10 palette
            palette = plt.get_cmap("tab10")
            colors[p] = palette(i % 10)
    return colors


def main():
    args = parse_args()
    weights_list, motor_stop_epoch, raw_data = load_data(args.json_file)

    if len(weights_list) == 0:
        raise SystemExit("No weight measurements found in 'weights' array.")

    # Prepare arrays
    indices, times_sec, vals, phases, t0 = prepare_arrays(weights_list)

    # Determine motor_stop relative seconds (if present)
    motor_stop_sec = None
    if motor_stop_epoch is not None:
        motor_stop_sec = float(motor_stop_epoch) - float(t0)

    # Compute regression for prediction phase only
    reg = compute_prediction_regression(times_sec, vals, phases)

    # Setup plot
    fig, ax = plt.subplots(figsize=(10, 6))

    # Color-code by phase
    unique_phases = list(dict.fromkeys(phases))  # preserve order
    phase_colors = make_phase_colors(unique_phases)

    phase_handles = []
    for p in unique_phases:
        mask = [ph == p for ph in phases]
        if not any(mask):
            continue
        h = ax.scatter(indices[mask], vals[mask], label=p, s=40, alpha=0.9, edgecolors="k", linewidths=0.3, zorder=3, color=phase_colors[p])
        phase_handles.append(h)

    # If regression exists, plot regression line across the entire time span
    other_handles = []
    if reg is not None:
        # To draw the regression line on the same x-axis (indices), we need a mapping
        # from index -> time. We'll use interpolation from indices->times_sec.
        # Build a dense set of x for smooth line
        i_dense = np.linspace(indices[0], indices[-1], 500)
        # Map to times
        times_from_indices = np.interp(i_dense, indices, times_sec)
        y_line = reg["slope"] * times_from_indices + reg["intercept"]
        line_handle, = ax.plot(i_dense, y_line, linestyle="--", linewidth=2.0, label="prediction fit", zorder=2, color=phase_colors.get("prediction", None))
        other_handles.append(line_handle)

        # Annotate slope & intercept and r value on plot (top-left)
        text_x = 0.02  # axes fraction
        text_y = 0.95
        r_text = f"fit (prediction): y = {reg['slope']:.3f}·t + {reg['intercept']:.3f}\nPearson r = {reg['r']:.3f}"
        ax.text(text_x, text_y, r_text, transform=ax.transAxes, fontsize=9, va="top", bbox=dict(boxstyle="round,pad=0.3", alpha=0.12))

    else:
        ax.text(0.02, 0.95, "Not enough 'prediction' points to fit regression", transform=ax.transAxes, fontsize=9, va="top", bbox=dict(boxstyle="round,pad=0.3", alpha=0.12))

    # Vertical line for motor stop (if present)
    if motor_stop_sec is not None:
        # Map motor_stop_sec -> index domain using interpolation
        # If motor_stop_sec outside measured times, np.interp will extrapolate with left/right values:
        motor_stop_idx = float(np.interp(motor_stop_sec, times_sec, indices, left=indices[0], right=indices[-1]))
        vline = ax.axvline(motor_stop_idx, linestyle="-", linewidth=1.8, label="motor stop", zorder=1, color="red")
        other_handles.append(vline)

        # Place label at bottom of the line (y at bottom of y-axis). We'll place slightly above bottom
        ymin, ymax = ax.get_ylim()
        y_text = ymin + 0.02 * (ymax - ymin)
        ax.text(motor_stop_idx, y_text, "motor stop", rotation=90, verticalalignment="bottom", horizontalalignment="left", fontsize=9, backgroundcolor=(1,1,1,0.6))

    # Axis labels and title
    ax.set_xlabel("Measurement index")
    ax.set_ylabel("Weight")
    ax.set_title(f"Weights vs Measurement Index — file: {args.json_file}")

    # Secondary x-axis showing time in seconds (step 0.1 s)
    # Use matplotlib's secondary_xaxis to map index <-> time
    # Functions must be vectorized: index -> time, time -> index
    def index_to_time(x):
        # x may be scalar or array
        return np.interp(x, indices, times_sec)

    def time_to_index(t):
        return np.interp(t, times_sec, indices)

    secax = ax.secondary_xaxis('top', functions=(index_to_time, time_to_index))
    secax.set_xlabel("Time (s)")

    # Set ticks every 0.1 seconds
    max_time = float(times_sec[-1])
    # ensure we include motor_stop if it's slightly beyond last time
    if motor_stop_sec is not None:
        max_time = max(max_time, motor_stop_sec)
    # create ticks with step 0.1
    # guard against floating point issues by rounding
    num_steps = int(math.ceil(max_time / 0.1)) if max_time > 0 else 1
    tick_times = np.round(np.arange(0.0, (num_steps + 1) * 0.1, 0.1), 3)
    secax.set_xticks(tick_times)
    # Optionally, shorten tick labels if too many: only show every nth tick to avoid clutter
    max_ticks_to_show = 25
    if len(tick_times) > max_ticks_to_show:
        # pick step to display approx max_ticks_to_show
        step = max(1, int(np.ceil(len(tick_times) / max_ticks_to_show)))
        display_ticks = tick_times[::step]
        secax.set_xticks(display_ticks)
    secax.set_xlim(index_to_time(indices[0]), index_to_time(indices[-1]))

    # Legend: ensure we pass only handles/labels lists (avoid passing Artists inadvertently)
    handles = phase_handles + other_handles
    labels = [h.get_label() for h in handles]
    ax.legend(handles, labels, loc="lower right")

    ax.grid(True, linestyle=":", linewidth=0.6, zorder=0, alpha=0.8)
    fig.tight_layout()

    if args.save:
        fig.savefig(args.save, dpi=200)
        print(f"Saved figure to {args.save}")
    else:
        plt.show()


if __name__ == "__main__":
    main()
