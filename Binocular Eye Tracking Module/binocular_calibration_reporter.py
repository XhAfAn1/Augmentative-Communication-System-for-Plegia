#!/usr/bin/env python3
"""
binocular_calibration_reporter.py
=================================
9-panel diagnostic chart for the binocular calibration.

Every panel is drawn from measured values in the metrics dictionary produced
by BinocularCalibrator. If a measurement is missing, the panel says so instead
of plotting an estimate.
"""

import os
import math
import time
from typing import Dict, Any, Optional, List
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
import matplotlib.patches as patches

PAL_BG     = "#080c14"
PAL_PANEL  = "#0f1623"
PAL_BORDER = "#1c2638"
PAL_CYAN   = "#00e5ff"
PAL_GOLD   = "#e3b341"
PAL_GREEN  = "#3fb950"
PAL_RED    = "#f85149"
PAL_TEXT   = "#f0f6fc"
PAL_MUTED  = "#8b949e"
PAL_DIM    = "#484f58"
PAL_ORANGE = "#ff9800"

PX_PER_DEG = 39.5  # approx. for a 1920px-wide 24" display at 60 cm; label only


def _na(ax, msg: str, keep_axes: bool = False):
    ax.text(0.5, 0.5, msg, transform=ax.transAxes, ha='center', va='center',
            fontsize=10, color=PAL_MUTED, style='italic')
    if not keep_axes:
        ax.set_xticks([])
        ax.set_yticks([])


def _bar_panel(ax, labels, values, colors, ylabel, ref_lines=(), hatch_last=False):
    """Bar chart that skips None values and labels them n/a."""
    xs = np.arange(len(labels))
    plot_vals = [0.0 if v is None else float(v) for v in values]
    bars = ax.bar(xs, plot_vals, width=0.55, color=colors, alpha=0.9)
    if hatch_last:
        bars[-1].set_hatch('//')
    for rect, v in zip(bars, values):
        txt = "n/a" if v is None else f"{v:.1f}px"
        ax.text(rect.get_x() + rect.get_width() / 2., rect.get_height() + 1.0, txt,
                ha='center', va='bottom', fontsize=8, color='#ffffff', fontweight='bold')
    for y, col, style, label in ref_lines:
        ax.axhline(y, color=col, linestyle=style, linewidth=1.1, label=label)
    ax.set_ylabel(ylabel, fontsize=9, color=PAL_MUTED)
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, fontsize=8.5, fontweight='bold')
    ax.grid(axis='y', linestyle='--', alpha=0.25)
    top = max([v for v in plot_vals] + [y for y, *_ in ref_lines] + [10.0])
    ax.set_ylim(0, top * 1.3)
    if ref_lines:
        ax.legend(loc='upper right', fontsize=7.5, facecolor=PAL_BG, edgecolor=PAL_BORDER)


def generate_binocular_chart(metrics: Dict[str, Any], save_path: str = "binocular_calibration_chart.png",
                             pursuit_records: Optional[List[Dict[str, Any]]] = None) -> str:
    """Renders the 9-panel diagnostic chart (3x3 grid, 200 DPI) and returns its absolute path."""
    sw = metrics.get("screen_w", 1920)
    sh = metrics.get("screen_h", 1080)
    mean_err = metrics.get("mean_error_px")
    p95_err = metrics.get("p95_error_px")
    l_mean = metrics.get("left_mean_error_px")
    r_mean = metrics.get("right_mean_error_px")
    m_jitter = metrics.get("mean_jitter")
    grade = metrics.get("grade", "N/A")
    grid_px = metrics.get("grid_px") or []
    val_px = metrics.get("val_px") or []
    pursuit_px = metrics.get("pursuit_px") or []

    plt.style.use('dark_background')
    fig = plt.figure(figsize=(22, 17), dpi=200, facecolor=PAL_BG)
    gs = GridSpec(3, 3, figure=fig, hspace=0.36, wspace=0.26, left=0.05, right=0.96, top=0.92, bottom=0.05)

    ts_now = metrics.get("calibrated_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    pupil_alg = metrics.get("pupil_algorithm", "Pupil Algorithm")
    model_type = "Thin Plate Spline (TPS 2D)" if (metrics.get("tps_left") or "Spline" in str(metrics.get("model_type", ""))) else "Bivariate Polynomial"
    fmt_err = f"{mean_err:.1f}px (~{mean_err / PX_PER_DEG:.2f}°)" if mean_err is not None else "N/A"
    fig.suptitle(f"BINOCULAR EYE TRACKING MODULE — DUAL-SENSOR CALIBRATION ACCURACY & DIAGNOSTIC REPORT\n"
                 f"Algorithm: {pupil_alg}  ·  Model: {model_type}  ·  Screen: {sw}x{sh}  ·  Status: {grade}  ·  Mean Error: {fmt_err}",
                 fontsize=14, fontweight='bold', color=PAL_CYAN, y=0.975)

    # ---------------------------------------------------------------- Panel 1
    ax1 = fig.add_subplot(gs[0, 0], facecolor=PAL_PANEL)
    ax1.set_title("1. VALIDATION ACCURACY", fontsize=11, fontweight='bold', color=PAL_TEXT, pad=8)
    if metrics.get("has_validation"):
        _bar_panel(ax1, ["Left Eye", "Right Eye", "Fused Gaze", "95% Worst"],
                   [l_mean, r_mean, mean_err, p95_err],
                   [PAL_CYAN, PAL_GOLD, PAL_GREEN, PAL_ORANGE], "Error (pixels)",
                   ref_lines=[(35.0, PAL_GREEN, '--', 'Clinical Target (35px / ~0.9°)'),
                              (60.0, PAL_GOLD, ':', 'AAC Usability Limit (60px / ~1.5°)')],
                   hatch_last=True)
    else:
        _na(ax1, "No validation points were collected")

    # ---------------------------------------------------------------- Panel 2
    ax2 = fig.add_subplot(gs[0, 1], facecolor=PAL_PANEL)
    sv_records = metrics.get("static_val_landings") or metrics.get("static_val_records") or []
    val_landings = [v for v in metrics.get("val_landings", []) if v.get("fused") is not None]

    title_suffix = ""
    if metrics.get("static_val_mean_err") is not None:
        title_suffix = f" (Mean: {metrics['static_val_mean_err']:.1f}px)"
    elif mean_err is not None:
        title_suffix = f" (Mean: {mean_err:.1f}px)"
    ax2.set_title(f"2. 9-POINT STATIC VALIDATION & DRIFT VECTORS{title_suffix}", fontsize=11, fontweight='bold', color=PAL_TEXT, pad=8)

    # Draw grid target anchor positions
    for gx, gy in grid_px:
        ax2.scatter(gx, gy, marker='+', s=120, color='#ffffff', linewidths=2.0, zorder=4)
        ax2.add_patch(patches.Circle((gx, gy), radius=35, edgecolor='#254d30', facecolor='none', linestyle='--', linewidth=1.1, zorder=3))

    has_drawn_any = False
    if sv_records:
        for i, r in enumerate(sv_records):
            tx, ty = r.get("target", (0, 0))
            gz = r.get("gaze")
            err = r.get("error_px")
            if gz is not None:
                gx, gy = gz
                if err is None or err >= 900.0:
                    err = math.hypot(gx - tx, gy - ty)
                col = PAL_GREEN if err <= 35.0 else (PAL_GOLD if err <= 60.0 else PAL_RED)
                ax2.scatter(gx, gy, color=col, s=65, alpha=0.95, zorder=6)
                ax2.annotate("", xy=(gx, gy), xytext=(tx, ty),
                             arrowprops=dict(arrowstyle="->", color=col, lw=1.6, alpha=0.9))
                ax2.text(tx, ty - 18, f"P{i + 1}: {err:.0f}px", color='#ffffff', fontsize=8, ha='center',
                         fontweight='bold', bbox=dict(boxstyle='round,pad=0.2', facecolor='#0b111a', edgecolor=col, alpha=0.85, lw=0.9), zorder=7)
                has_drawn_any = True
            else:
                ax2.text(tx, ty - 18, f"P{i + 1}: Miss", color=PAL_RED, fontsize=8, ha='center',
                         fontweight='bold', bbox=dict(boxstyle='round,pad=0.2', facecolor='#0b111a', edgecolor=PAL_RED, alpha=0.85, lw=0.9), zorder=7)

    if val_landings:
        for i, vl in enumerate(val_landings):
            tx, ty = vl["target"]
            fx, fy = vl["fused"]
            err = math.hypot(fx - tx, fy - ty)
            ax2.scatter(tx, ty, marker='d', s=100, color='#00e5ff', linewidths=1.5, zorder=5)
            col = PAL_GREEN if err <= 35.0 else (PAL_GOLD if err <= 60.0 else PAL_RED)
            ax2.scatter(fx, fy, color=col, s=50, alpha=0.95, zorder=6)
            ax2.annotate("", xy=(fx, fy), xytext=(tx, ty), arrowprops=dict(arrowstyle="->", color=col, lw=1.3, alpha=0.8))
            ax2.text(tx, ty + 18, f"V#{i + 1}: {err:.0f}px", color='#00e5ff', fontsize=7.5, ha='center', zorder=7)
            has_drawn_any = True

    if not has_drawn_any:
        _na(ax2, "No validation landings recorded", keep_axes=True)

    ax2.scatter([], [], marker='+', color='#ffffff', s=80, label='Target Anchor')
    ax2.scatter([], [], color=PAL_GREEN, s=40, label='Landing (<35px Clinical)')
    ax2.scatter([], [], color=PAL_GOLD, s=40, label='Landing (<60px Usable)')
    ax2.scatter([], [], color=PAL_RED, s=40, label='Landing (>60px Drift)')
    ax2.plot([], [], color=PAL_GREEN, linestyle='--', label='35px Clinical Circle')
    ax2.legend(loc='lower right', fontsize=7.5, facecolor=PAL_BG, edgecolor=PAL_BORDER)
    ax2.set_xlim(0, sw)
    ax2.set_ylim(sh, 0)
    ax2.set_xlabel("Screen X (px)", fontsize=8.5, color=PAL_MUTED)
    ax2.set_ylabel("Screen Y (px)", fontsize=8.5, color=PAL_MUTED)
    ax2.grid(True, linestyle='--', alpha=0.2)

    # ---------------------------------------------------------------- Panel 3
    ax3 = fig.add_subplot(gs[0, 2], facecolor=PAL_PANEL)
    ax3.set_title("3. FIXATION PRECISION (SCREEN-SPACE JITTER)", fontsize=11, fontweight='bold', color=PAL_TEXT, pad=8)
    series = [(metrics.get("jitters_left") or [], "Left Eye", PAL_CYAN),
              (metrics.get("jitters_right") or [], "Right Eye", PAL_GOLD),
              (metrics.get("jitters") or [], "Fused Gaze", PAL_GREEN)]
    series = [s for s in series if s[0]]
    if series:
        bp = ax3.boxplot([s[0] for s in series], tick_labels=[s[1] for s in series],
                         patch_artist=True, showmeans=True,
                         meanprops={"marker": "o", "markerfacecolor": "#ffffff", "markeredgecolor": "#ffffff", "markersize": 5})
        for patch, s in zip(bp['boxes'], series):
            patch.set_facecolor(s[2])
            patch.set_alpha(0.65)
        ax3.axhline(6.0, color=PAL_GREEN, linestyle='--', linewidth=1.1, label='Low Tremor (< 6px)')
        ax3.axhline(12.0, color=PAL_GOLD, linestyle=':', linewidth=1.1, label='Moderate Jitter (< 12px)')
        ax3.legend(loc='upper right', fontsize=7.5, facecolor=PAL_BG, edgecolor=PAL_BORDER)
        if m_jitter is not None:
            ax3.text(0.5, 0.02, f"Mean Fused Jitter: {m_jitter:.1f}px (~{m_jitter / PX_PER_DEG:.2f}°)",
                     transform=ax3.transAxes, ha='center', fontsize=8.5, color=PAL_GREEN, fontweight='bold')
    else:
        _na(ax3, "No jitter samples recorded")
    ax3.set_ylabel("Mean radial deviation during fixation (screen px)", fontsize=9, color=PAL_MUTED)
    ax3.grid(axis='y', linestyle='--', alpha=0.25)

    # ---------------------------------------------------------------- Panel 4
    ax4 = fig.add_subplot(gs[1, 0], facecolor=PAL_PANEL)
    ax4.set_title("4. SMOOTH PURSUIT TRAJECTORY (UNFILTERED GAZE)", fontsize=11, fontweight='bold', color=PAL_TEXT, pad=8)
    for p1, p2 in pursuit_px:
        ax4.plot([p1[0], p2[0]], [p1[1], p2[1]], color='#ffffff', linestyle='--', linewidth=1.4, alpha=0.5)
    by_path: Dict[int, List] = {}
    for r in (pursuit_records or []):
        g = r.get("raw_fused")
        if g is None or r.get("is_blink"):
            continue
        by_path.setdefault(r.get("path_idx", 0), []).append(g)
    if by_path:
        for pid, pts in by_path.items():
            if len(pts) > 2:
                ax4.plot([p[0] for p in pts], [p[1] for p in pts], color=PAL_CYAN, linewidth=1.4, alpha=0.85, zorder=5)
        p_mean = metrics.get("pursuit_mean_px")
        p_rms = metrics.get("pursuit_rmse")
        p_comp = metrics.get("pursuit_compensated_mean_px")
        drop = metrics.get("pursuit_dropout_frames", 0)
        blink = metrics.get("pursuit_blink_frames", 0)
        lab = "Gaze"
        if p_mean is not None:
            lab += f" (mean {p_mean:.0f}px · RMS {p_rms:.0f}px"
            if p_comp is not None:
                lab += f" · lag-comp {p_comp:.0f}px"
            lab += ")"
        ax4.plot([], [], color='#ffffff', linestyle='--', label='Target Path')
        ax4.plot([], [], color=PAL_CYAN, label=lab)
        ax4.plot([], [], color='none', label=f"{metrics.get('pursuit_count', 0)} tracked · {drop} dropouts · {blink} blinks")
        ax4.legend(loc='lower right', fontsize=7.5, facecolor=PAL_BG, edgecolor=PAL_BORDER)
    else:
        _na(ax4, "No pursuit frames with a gaze prediction", keep_axes=True)
    ax4.set_xlim(0, sw)
    ax4.set_ylim(sh, 0)
    ax4.set_xlabel("Screen X (px)", fontsize=8.5, color=PAL_MUTED)
    ax4.set_ylabel("Screen Y (px)", fontsize=8.5, color=PAL_MUTED)
    ax4.grid(True, linestyle='--', alpha=0.2)

    # ---------------------------------------------------------------- Panel 5
    ax5 = fig.add_subplot(gs[1, 1], facecolor=PAL_PANEL)
    ax5.set_title("5. STATIC ANCHOR RESIDUALS (TRAINING FIT PER POINT)", fontsize=11, fontweight='bold', color=PAL_TEXT, pad=8)
    tl = metrics.get("train_left") or {}
    tr = metrics.get("train_right") or {}
    res_l = tl.get("residuals")
    res_r = tr.get("residuals")

    if (res_l and len(res_l) >= 9) or (res_r and len(res_r) >= 9):
        n_pts = max(len(res_l or []), len(res_r or []))
        xs = np.arange(n_pts)
        w5 = 0.38
        clean_l = [0.0 if (not res_l or i >= len(res_l) or res_l[i] is None) else float(res_l[i]) for i in range(n_pts)]
        clean_r = [0.0 if (not res_r or i >= len(res_r) or res_r[i] is None) else float(res_r[i]) for i in range(n_pts)]
        b1 = ax5.bar(xs - w5 / 2, clean_l, width=w5, color=PAL_CYAN, label='Left Eye', alpha=0.9)
        b2 = ax5.bar(xs + w5 / 2, clean_r, width=w5, color=PAL_GOLD, label='Right Eye', alpha=0.9)
        for rect, val in zip(b1, clean_l):
            if val > 0:
                ax5.text(rect.get_x() + rect.get_width() / 2., rect.get_height() + 0.8, f"{val:.0f}",
                         ha='center', va='bottom', fontsize=7, color='#ffffff', fontweight='bold')
        for rect, val in zip(b2, clean_r):
            if val > 0:
                ax5.text(rect.get_x() + rect.get_width() / 2., rect.get_height() + 0.8, f"{val:.0f}",
                         ha='center', va='bottom', fontsize=7, color='#ffffff', fontweight='bold')
        ax5.axhline(35.0, color=PAL_GREEN, linestyle='--', linewidth=1.1, label='Clinical Target (35px)')
        ax5.axhline(60.0, color=PAL_GOLD, linestyle=':', linewidth=1.1, label='AAC Limit (60px)')
        ax5.set_xticks(xs)
        ax5.set_xticklabels([f"P{i + 1}" for i in range(n_pts)], fontsize=8.5, fontweight='bold')
        ax5.set_ylabel("Anchor residual (pixels)", fontsize=9, color=PAL_MUTED)
        ax5.legend(loc='upper right', fontsize=7.5, facecolor=PAL_BG, edgecolor=PAL_BORDER)
        top = max(clean_l + clean_r + [65.0])
        ax5.set_ylim(0, top * 1.35)
        ax5.grid(axis='y', linestyle='--', alpha=0.25)
        ax5.text(0.5, 0.93, f"Anchors fitted: L {tl.get('points_used', 0)}/{n_pts} · R {tr.get('points_used', 0)}/{n_pts}",
                 transform=ax5.transAxes, ha='center', fontsize=8, color=PAL_MUTED)
    elif tl.get("rms") is not None or tr.get("rms") is not None:
        _bar_panel(ax5, ["Left X", "Left Y", "Right X", "Right Y", "Val Mean", "Val P95"],
                   [tl.get("rms_x"), tl.get("rms_y"), tr.get("rms_x"), tr.get("rms_y"), mean_err, p95_err],
                   [PAL_CYAN, PAL_CYAN, PAL_GOLD, PAL_GOLD, PAL_GREEN, PAL_ORANGE], "RMS residual (pixels)",
                   ref_lines=[(35.0, PAL_GREEN, '--', 'Clinical Target (35px)')])
        ax5.text(0.5, 0.93, f"Anchors used: L {tl.get('points_used', 0)}/9 · R {tr.get('points_used', 0)}/9",
                 transform=ax5.transAxes, ha='center', fontsize=8, color=PAL_MUTED)
    else:
        _na(ax5, "No training residuals available")

    # ---------------------------------------------------------------- Panel 6
    ax6 = fig.add_subplot(gs[1, 2], facecolor=PAL_PANEL)
    ax6.set_title("6. REGIONAL ACCURACY (UP / CENTER / DOWN)", fontsize=11, fontweight='bold', color=PAL_TEXT, pad=8)
    reg = metrics.get("regional") or {}
    reg_vals = [reg.get(k, {}).get("mean_error_px") for k in ("up", "center", "down")]
    reg_n = [reg.get(k, {}).get("samples", 0) for k in ("up", "center", "down")]
    if any(v is not None for v in reg_vals):
        _bar_panel(ax6, [f"Upward Gaze\n(Y < 35%) n={reg_n[0]}", f"Center Gaze\nn={reg_n[1]}", f"Downward Gaze\n(Y > 65%) n={reg_n[2]}"],
                   reg_vals, [PAL_CYAN, PAL_GREEN, PAL_GOLD], "Mean error (pixels)")
        ax6.text(0.5, 0.93, "Validation landings + lag-compensated pursuit frames",
                 transform=ax6.transAxes, ha='center', fontsize=8, color=PAL_MUTED)
    else:
        _na(ax6, "No regional data")

    # ---------------------------------------------------------------- Panel 7
    ax7 = fig.add_subplot(gs[2, 0], facecolor=PAL_PANEL)
    ax7.set_title("7. STATIC POINT SIGNAL QUALITY (ACCEPTED FRAMES)", fontsize=11, fontweight='bold', color=PAL_TEXT, pad=8)
    ql = [100.0 * q for q in (metrics.get("static_quality_left") or [])]
    qr = [100.0 * q for q in (metrics.get("static_quality_right") or [])]
    n_pts = max(len(ql), len(qr))
    if n_pts:
        x7 = np.arange(n_pts)
        w7 = 0.35
        ql += [0.0] * (n_pts - len(ql))
        qr += [0.0] * (n_pts - len(qr))
        ax7.bar(x7 - w7 / 2, ql, width=w7, color=PAL_CYAN, label='Left Eye', alpha=0.9)
        ax7.bar(x7 + w7 / 2, qr, width=w7, color=PAL_GOLD, label='Right Eye', alpha=0.9)
        ax7.axhline(75.0, color=PAL_GOLD, linestyle=':', linewidth=1.1, label='Min Acceptable (75%)')
        ax7.set_xticks(x7)
        ax7.set_xticklabels([f"P{i + 1}" for i in range(n_pts)], fontsize=8.5, fontweight='bold')
        ax7.set_ylim(0, 118)
        ax7.legend(loc='lower right', fontsize=7.5, facecolor=PAL_BG, edgecolor=PAL_BORDER)
    else:
        _na(ax7, "No per-point quality recorded")
    ax7.set_ylabel("Frames with accepted pupil (%)", fontsize=9, color=PAL_MUTED)
    ax7.grid(axis='y', linestyle='--', alpha=0.25)

    # ---------------------------------------------------------------- Panel 8
    ax8 = fig.add_subplot(gs[2, 1], facecolor=PAL_PANEL)
    ax8.set_title("8. PREDICTED AAC KEY HIT RATE BY KEY SIZE", fontsize=11, fontweight='bold', color=PAL_TEXT, pad=8)
    b_rates = [metrics.get("hit_40"), metrics.get("hit_60"), metrics.get("hit_80"), metrics.get("hit_100")]
    if all(v is not None for v in b_rates):
        x8 = np.arange(4)
        bars8 = ax8.bar(x8, b_rates, width=0.52, color=['#00aaff', '#00ddbb', PAL_GREEN, '#80e040'], alpha=0.9)
        ax8.axhline(90.0, color=PAL_GREEN, linestyle='--', linewidth=1.2, label='Clinical Threshold (90%)')
        ax8.set_xticks(x8)
        ax8.set_xticklabels(['40px (Dense)', '60px (Standard)', '80px (Accessible)', '100px (Macro)'], fontsize=8, fontweight='bold')
        for rect, rate in zip(bars8, b_rates):
            ax8.text(rect.get_x() + rect.get_width() / 2., rect.get_height() + 1.8, f"{rate:.1f}%",
                     ha='center', va='bottom', fontsize=8, color='#ffffff', fontweight='bold')
        ax8.legend(loc='lower right', fontsize=7.5, facecolor=PAL_BG, edgecolor=PAL_BORDER)
        ax8.text(0.5, 0.93, "Rayleigh model from mean validation error (isotropic Gaussian landing)",
                 transform=ax8.transAxes, ha='center', fontsize=7.5, color=PAL_MUTED)
    else:
        _na(ax8, "Requires validation data")
    ax8.set_ylabel("Hit rate (%)", fontsize=9, color=PAL_MUTED)
    ax8.set_ylim(0, 118)
    ax8.grid(axis='y', linestyle='--', alpha=0.25)

    # ---------------------------------------------------------------- Panel 9
    ax9 = fig.add_subplot(gs[2, 2], polar=True, facecolor=PAL_PANEL)
    ax9.set_title("9. CLINICAL AAC SUITABILITY RADAR", fontsize=11, fontweight='bold', color=PAL_TEXT, pad=14)

    def score(val, scale):
        return None if val is None else max(0.0, min(100.0, 100.0 - val * scale))

    q_all = (metrics.get("static_quality_left") or []) + (metrics.get("static_quality_right") or [])
    axes = [
        ('Accuracy\n(Low Error)', score(mean_err, 1.2)),
        ('Stability\n(Low Jitter)', score(m_jitter, 8.0)),
        ('Binocular\nSymmetry', score(abs(l_mean - r_mean), 5.0) if (l_mean is not None and r_mean is not None) else None),
        ('Model Fit\n(Low RMS)', score(max(v for v in (tl.get("rms"), tr.get("rms")) if v is not None), 1.5) if (tl.get("rms") or tr.get("rms")) else None),
        ('Signal\nQuality', 100.0 * float(np.mean(q_all)) if q_all else None),
        ('AAC Key\nUsability', metrics.get("hit_60")),
    ]
    cats = [c + ("" if v is not None else "\n(n/a)") for c, v in axes]
    values = [0.0 if v is None else v for _, v in axes]
    values += values[:1]
    N = len(axes)
    angles = [n / float(N) * 2 * math.pi for n in range(N)]
    angles += angles[:1]
    ax9.set_theta_offset(math.pi / 2)
    ax9.set_theta_direction(-1)
    ax9.set_rlabel_position(0)
    ax9.set_xticks(angles[:-1])
    ax9.set_xticklabels(cats, color='#b0b0b0', size=7.5)
    ax9.set_yticks([25, 50, 75, 100])
    ax9.set_yticklabels(["25", "50", "75", "100"], color='#607080', size=6.5)
    ax9.set_ylim(0, 100)
    ax9.plot(angles, values, linewidth=1.8, color=PAL_CYAN)
    ax9.fill(angles, values, color=PAL_CYAN, alpha=0.30)
    ax9.grid(color='#25354d', linestyle=':')

    tmp_path = save_path + ".tmp.png"
    plt.savefig(tmp_path, facecolor=PAL_BG, edgecolor='none', dpi=200)
    plt.close(fig)
    os.replace(tmp_path, save_path)
    return os.path.abspath(save_path)
