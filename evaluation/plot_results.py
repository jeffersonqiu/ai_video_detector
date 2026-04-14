"""
plot_results.py — generate cost-vs-trial chart from results.jsonl
"""
import json
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from pathlib import Path

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

results = []
for i, line in enumerate(Path("results.jsonl").read_text().splitlines()):
    line = line.strip()
    if not line:
        continue
    d = json.loads(line)
    results.append({
        "trial": i + 1,
        "strategy": d["strategy_name"],
        "pass_rate": d["pass_rate"],
        "cost": d["total_cost_usd"],
        "valid": d["is_valid"],
        "new_best": d["is_new_best"],
    })

trials    = [r["trial"]    for r in results]
costs     = [r["cost"]     for r in results]
valid     = [r["valid"]    for r in results]
new_best  = [r["new_best"] for r in results]
pass_rate = [r["pass_rate"] for r in results]
strategies = [r["strategy"] for r in results]

# ---------------------------------------------------------------------------
# Short labels per trial
# ---------------------------------------------------------------------------

labels = [
    "1 Baseline\n(bug: 1/5)",
    "2 Baseline\nfixed (4/5)",
    "3 Flash+20f\n+timelapse\nprompt ✓",
    "4 Caption\nfast-path\n+Flash 20f ✓",
    "5 Flash\n12f (3/5)",
    "6 Flash\n16f (4/5)",
    "7 Caption\n+Flash 18f ✓",
    "8 Flash-Lite\n20f (3/5)",
    "9 Routing:\nLite vs\nFlash ✓",
    "10 No audio\nFlash (4/5)",
    "11 Scoping\nbug (1/5)",
    "12 Routing\nFlash18\nLite20 ✓",
    "13 Routing\nFlash18\nLite15 ✓",
    "14 Short\nprompt\n(3/5)",
]

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------

VALID_NEW   = "#22c55e"   # green  — valid AND new cost record
VALID_OLD   = "#86efac"   # light green — valid but not a new record
INVALID     = "#f87171"   # red    — failed (accuracy < 100%)
BUG         = "#d1d5db"   # grey   — bug / scoping error

def bar_color(r):
    if r["cost"] == 0 and not r["valid"]:
        return BUG
    if r["valid"] and r["new_best"]:
        return VALID_NEW
    if r["valid"]:
        return VALID_OLD
    return INVALID

colors = [bar_color(r) for r in results]

# ---------------------------------------------------------------------------
# Best-so-far ratchet line
# ---------------------------------------------------------------------------

best_line = []
best = float("inf")
for r in results:
    if r["valid"] and r["cost"] < best:
        best = r["cost"]
    best_line.append(best if best != float("inf") else None)

# ---------------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------------

fig, ax = plt.subplots(figsize=(18, 8))
fig.patch.set_facecolor("#0f172a")
ax.set_facecolor("#1e293b")

x = np.arange(len(results))
bar_width = 0.62

bars = ax.bar(x, costs, width=bar_width, color=colors, zorder=3, linewidth=0)

# Ratchet line (best valid cost so far)
valid_best_y = [b for b in best_line]
ax.step(x, valid_best_y, where="post", color="#facc15", linewidth=2.2,
        linestyle="--", zorder=4, label="Best valid cost so far")

# Pass-rate annotation on each bar
for i, (r, bar) in enumerate(zip(results, bars)):
    h = bar.get_height()
    if h == 0:
        ax.text(i, 0.00015, r["pass_rate"], ha="center", va="bottom",
                color="#9ca3af", fontsize=7.5, fontweight="bold")
    else:
        ax.text(i, h + 0.00015, r["pass_rate"], ha="center", va="bottom",
                color="white", fontsize=7.5, fontweight="bold")

# Per-trial finding notes — placed at staggered heights with arrows to bar tops
# (trial_index, text, y_text_offset_from_max, x_nudge)
max_cost = max(costs)
FINDINGS = {
    #  trial:  (label text,                                        y_frac,   x_nudge)
    3:  ("★ First valid 5/5\n+Timelapse prompt +20 frames\ncatches subtle AI timelapse",       1.46, -1.1),
    4:  ("★ −25% vs T3\nCaption fast-path: skip model\nfor 'AI or Real' caption",             1.46,  1.2),
    7:  ("★ −4% vs T4\nMin frames = 18 for\nDWmajXxjF7S (flowerbed)",                         1.46,  0.0),
    9:  ("★ −29% vs T7\nRouting: Flash-Lite (3× cheaper)\nfor non-timelapse content",          1.46,  0.0),
    12: ("★ −5% vs T9\nFlash tier trimmed\n20 → 18 frames",                                   1.46,  0.6),
}

for i, r in enumerate(results):
    trial_num = i + 1
    if r["new_best"] and trial_num in FINDINGS:
        note, y_frac, x_nudge = FINDINGS[trial_num]
        bar_top = r["cost"]
        text_y  = max_cost * y_frac * 0.70
        ax.annotate(
            note,
            xy=(i, bar_top),
            xytext=(i + x_nudge, text_y),
            color="#facc15", fontsize=7.0,
            fontweight="bold",
            linespacing=1.4,
            ha="center", va="bottom",
            bbox=dict(boxstyle="round,pad=0.28", facecolor="#0f172a",
                      edgecolor="#facc15", linewidth=0.9, alpha=0.92),
            arrowprops=dict(arrowstyle="-|>", color="#facc15",
                            lw=1.1, mutation_scale=8),
        )

# X-axis
ax.set_xticks(x)
ax.set_xticklabels(labels, fontsize=7.8, color="#e2e8f0")
ax.tick_params(axis="x", length=0, pad=6)

# Y-axis — plain USD
ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:.4f}"))
ax.tick_params(axis="y", colors="#94a3b8", labelsize=9)
ax.set_ylabel("Total cost per 5-video run (USD)", color="#94a3b8", fontsize=10, labelpad=10)
ax.spines[["top","right","left","bottom"]].set_visible(False)
ax.yaxis.grid(True, color="#334155", linewidth=0.7, zorder=0)
ax.set_xlim(-0.6, len(results) - 0.4)
ax.set_ylim(0, max(costs) * 1.60)

# Title
ax.set_title(
    "AI Video Detector — Strategy Cost Optimization\n"
    "5-video test set · 100% accuracy required · lower = better",
    color="white", fontsize=13, fontweight="bold", pad=16
)

# Legend
legend_patches = [
    mpatches.Patch(color=VALID_NEW, label="Valid (5/5) — new cost record ★"),
    mpatches.Patch(color=VALID_OLD, label="Valid (5/5) — not a new record"),
    mpatches.Patch(color=INVALID,   label="Invalid (< 5/5 accuracy)"),
    mpatches.Patch(color=BUG,       label="Bug / zero-cost error"),
    plt.Line2D([0], [0], color="#facc15", linewidth=2, linestyle="--",
               label="Best valid cost ratchet"),
]
ax.legend(handles=legend_patches, loc="upper right", framealpha=0.15,
          labelcolor="white", fontsize=8.5, facecolor="#1e293b",
          edgecolor="#475569")

# Final best summary in top-left corner
final_best_idx = max((i for i, r in enumerate(results) if r["new_best"]), default=None)
if final_best_idx is not None:
    fb = results[final_best_idx]
    ax.text(
        0.01, 0.97,
        f"Final best: ${fb['cost']:.5f}  ({fb['strategy']})\n"
        f"vs first valid: $0.01341  →  51% total cost reduction",
        transform=ax.transAxes,
        color="#facc15", fontsize=8.5, va="top",
        bbox=dict(boxstyle="round,pad=0.4", facecolor="#1e293b",
                  edgecolor="#facc15", linewidth=1.0, alpha=0.9),
    )

plt.tight_layout()
out = Path("cost_vs_trial.png")
plt.savefig(out, dpi=160, bbox_inches="tight", facecolor=fig.get_facecolor())
print(f"Saved → {out.resolve()}")
plt.show()
