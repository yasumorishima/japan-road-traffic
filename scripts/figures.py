"""Figures for the README, drawn from the Kaggle build (hourly Parquet files), in a light and a dark version.

  python scripts/figures.py <build dir> <out dir>

Only permanent counters are used, and rows the source flags as faulty or missing are dropped (a failed counter
reports 0 vehicles with a flag)."""
import glob, os, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
import numpy as np
import pandas as pd

THEMES = {
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", muted="#898781", grid="#e1e0d9",
                  base="#c3c2b7", line="#2a78d6"),
    "dark": dict(surface="#1a1a19", ink="#ffffff", ink2="#c3c2b7", muted="#898781", grid="#2c2c2a",
                 base="#383835", line="#3987e5"),
}
BLUES = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
PARTS = ["up_small", "up_large", "up_unclassified", "down_small", "down_large", "down_unclassified"]
FLAGS = [f"{d}_{k}" for d in ("up", "down") for k in ("power_failure", "loop_fault", "ultrasonic_fault", "missing")]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def load(build):
    files = sorted(glob.glob(os.path.join(build, "hourly_*.parquet")))
    if not files:
        sys.exit("no hourly files in the build")
    cols = ["time_jst", "counter_id", "sensor"] + PARTS + FLAGS
    h = pd.concat([pd.read_parquet(p, columns=cols) for p in files], ignore_index=True)
    h = h[h.sensor == "loop"]
    h = h[~h[FLAGS].fillna(False).any(axis=1)].copy()
    h[PARTS] = h[PARTS].astype("float64")
    h["vehicles"] = h[PARTS].sum(axis=1, min_count=6)
    return h.dropna(subset=["vehicles"])


def style(ax, t):
    ax.set_facecolor(t["surface"])
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(t["base"])
    ax.tick_params(colors=t["muted"], labelsize=12)
    ax.grid(axis="y", color=t["grid"], linewidth=0.8)
    ax.set_axisbelow(True)


def figure(t, w=11, h=4.6):
    fig, ax = plt.subplots(figsize=(w, h), facecolor=t["surface"])
    style(ax, t)
    return fig, ax


def title(fig, ax, t, main, sub):
    ax.set_title(main, loc="left", fontsize=16, color=t["ink"], pad=26)
    ax.text(0, 1.02, sub, transform=ax.transAxes, fontsize=12, color=t["ink2"], va="bottom")


def main():
    build, out = sys.argv[1:3]
    os.makedirs(out, exist_ok=True)
    h = load(build)
    last = h.time_jst.max().date()
    span = f"{h.time_jst.min():%Y-%m-%d} to {last:%Y-%m-%d}, permanent counters"
    h["hour"] = h.time_jst.dt.hour
    h["weekday"] = h.time_jst.dt.dayofweek

    grid = h.groupby(["weekday", "hour"]).vehicles.mean().unstack().reindex(index=range(7), columns=range(24))
    large = h.groupby("hour").apply(lambda g: (g.up_large.sum() + g.down_large.sum()) / g.vehicles.sum() * 100)
    daily = (h.assign(date=h.time_jst.dt.normalize()).groupby(["counter_id", "date"])
               .agg(vehicles=("vehicles", "sum"), hours=("vehicles", "count")).reset_index())
    daily = daily[daily.hours == 24]
    daily["weekday"] = daily.date.dt.dayofweek
    usual = daily.groupby(["counter_id", "weekday"]).vehicles.transform("median")
    n = daily.groupby(["counter_id", "weekday"]).vehicles.transform("size")
    daily["ratio"] = np.where((n >= 5) & (usual > 0), daily.vehicles / usual, np.nan)
    national = daily.dropna(subset=["ratio"]).groupby("date").ratio.median()

    for name, t in THEMES.items():
        # 1. weekday x hour
        fig, ax = figure(t, 11, 4.4)
        # light: pale = little traffic; dark: the ramp runs from near the surface to bright
        ramp = BLUES if name == "light" else ["#104281", "#1c5cab", "#2a78d6", "#5598e7", "#86b6ef", "#cde2fb"]
        cmap = LinearSegmentedColormap.from_list("blues", ramp)
        im = ax.imshow(grid.values, aspect="auto", cmap=cmap)
        ax.set_yticks(range(7), DAYS)
        ax.set_xticks(range(0, 24, 3), [f"{x}:00" for x in range(0, 24, 3)])
        ax.grid(False)
        for s in ax.spines.values():
            s.set_visible(False)
        cb = fig.colorbar(im, ax=ax, pad=0.015)
        cb.outline.set_visible(False)
        cb.ax.tick_params(colors=t["muted"], labelsize=11)
        cb.set_label("vehicles per hour", color=t["ink2"], fontsize=12)
        title(fig, ax, t, "Traffic by weekday and hour", f"Mean vehicles per hour at a counter (both directions), {span}")
        fig.tight_layout()
        fig.savefig(os.path.join(out, f"weekday_hour_{name}.png"), dpi=110, facecolor=t["surface"])
        plt.close(fig)

        # 2. share of large vehicles by hour
        fig, ax = figure(t, 11, 4.2)
        ax.plot(large.index, large.values, color=t["line"], linewidth=2)
        ax.scatter(large.index, large.values, color=t["line"], s=36, zorder=3, edgecolors=t["surface"], linewidths=1.5)
        peak = large.idxmax()
        ax.annotate(f"{large.max():.0f}% at {peak}:00", (peak, large.max()), xytext=(-8, 8),
                    textcoords="offset points", color=t["ink"], fontsize=12, va="bottom", ha="right")
        ax.set_xticks(range(0, 24, 3), [f"{x}:00" for x in range(0, 24, 3)])
        ax.set_ylabel("large vehicles (%)", color=t["ink2"], fontsize=13)
        ax.set_ylim(0, large.max() * 1.15)
        title(fig, ax, t, "Large vehicles by hour", f"Share of large vehicles among all counted vehicles, by hour, {span}")
        fig.tight_layout()
        fig.savefig(os.path.join(out, f"large_share_{name}.png"), dpi=110, facecolor=t["surface"])
        plt.close(fig)

        # 3. nationwide ratio to a usual day
        fig, ax = figure(t, 11, 4.4)
        ax.axhline(1, color=t["base"], linewidth=1.2)
        ax.plot(national.index, national.values, color=t["line"], linewidth=2)
        peaks = []  # the highest day of each separate peak (at least 7 days apart), up to 3
        for d, r in national.sort_values(ascending=False).items():
            if r > 1.05 and all(abs((d - p).days) >= 7 for p, _ in peaks):
                peaks.append((d, r))
            if len(peaks) == 3:
                break
        for d, r in peaks:
            ax.scatter([d], [r], color=t["line"], s=40, zorder=3, edgecolors=t["surface"], linewidths=1.5)
            ax.annotate(f"{d:%b %d} ({d:%a})  ×{r:.2f}", (d, r), xytext=(0, 9), textcoords="offset points",
                        color=t["ink"], fontsize=11, ha="center", va="bottom")
        ax.set_ylim(min(0.9, national.min() - 0.02), national.max() + 0.05)
        ax.set_ylabel("ratio to the usual day", color=t["ink2"], fontsize=13)
        ax.text(national.index[0], 1.0, " usual", color=t["muted"], fontsize=11, va="bottom")
        title(fig, ax, t, "Holiday periods stand out",
              "Median over counters of each day's traffic ÷ the same counter's median for that weekday")
        fig.autofmt_xdate(rotation=0, ha="center")
        fig.tight_layout()
        fig.savefig(os.path.join(out, f"vs_usual_{name}.png"), dpi=110, facecolor=t["surface"])
        plt.close(fig)
    print(f"figures through {last} -> {out}")


if __name__ == "__main__":
    main()
