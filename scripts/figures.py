"""Figures for the README, drawn from the Kaggle build (hourly Parquet files), in a light and a dark version.

  python scripts/figures.py <build dir> <out dir>

Only permanent counters are used, and rows the source flags as faulty or missing are dropped (a failed counter
reports 0 vehicles with a flag)."""
import glob, os, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

THEMES = {
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", muted="#898781", grid="#e1e0d9",
                  base="#c3c2b7", line="#2a78d6", accent="#d9711c"),
    "dark": dict(surface="#1a1a19", ink="#ffffff", ink2="#c3c2b7", muted="#898781", grid="#2c2c2a",
                 base="#383835", line="#3987e5", accent="#f0913a"),
}


def load(build):
    files = sorted(glob.glob(os.path.join(build, "hourly_*.parquet")))
    if not files:
        sys.exit("no hourly files in the build")
    # vehicles (derived in build_kaggle.py) is empty for hours a permanent counter flags as faulty or missing
    cols = ["time_jst", "counter_id", "sensor", "up_large", "down_large", "vehicles", "is_holiday"]
    h = pd.concat([pd.read_parquet(p, columns=cols) for p in files], ignore_index=True)
    h = h[(h.sensor == "loop") & h.vehicles.notna()].copy()
    for c in ("up_large", "down_large", "vehicles"):
        h[c] = h[c].astype("float64")
    return h


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

    workday = (h.weekday < 5) & ~h.is_holiday.astype(bool)
    shape = {"Workdays": h[workday].groupby("hour").vehicles.mean().reindex(range(24)),
             "Weekends and holidays": h[~workday].groupby("hour").vehicles.mean().reindex(range(24))}
    wd, we = shape["Workdays"], shape["Weekends and holidays"]
    am, pm = int(wd.loc[5:11].idxmax()), int(wd.loc[14:20].idxmax())
    # weekends and holidays: the hours that stay within 7% of their busiest hour, as one run around it
    top = int(we.idxmax())
    lo = hi = top
    while lo > 0 and we[lo - 1] >= 0.93 * we[top]:
        lo -= 1
    while hi < 23 and we[hi + 1] >= 0.93 * we[top]:
        hi += 1
    # the title claims two workday rushes and a long weekend plateau only when the curves have that shape:
    # workdays dip at least 10% between the rushes; the weekend plateau lasts at least five hours
    two_rushes = wd.loc[am:pm].min() < 0.90 * min(wd[am], wd[pm])
    plateau = hi - lo + 1 >= 5
    if two_rushes and plateau:
        shape_title = f"Workdays have two rush hours; weekends and holidays stay busy from {lo}:00 to {hi + 1}:00"
    else:
        shape_title = f"Workdays peak at {am}:00 and {pm}:00; weekends and holidays at {top}:00"
    large = h.groupby("hour").apply(lambda g: (g.up_large.sum() + g.down_large.sum()) / g.vehicles.sum() * 100)
    daily = (h.assign(date=h.time_jst.dt.normalize()).groupby(["counter_id", "date"])
               .agg(vehicles=("vehicles", "sum"), hours=("vehicles", "count")).reset_index())
    daily = daily[daily.hours == 24]
    daily["weekday"] = daily.date.dt.dayofweek
    usual = daily.groupby(["counter_id", "weekday"]).vehicles.transform("median")
    n = daily.groupby(["counter_id", "weekday"]).vehicles.transform("size")
    daily["ratio"] = np.where((n >= 5) & (usual > 0), daily.vehicles / usual, np.nan)
    national = daily.dropna(subset=["ratio"]).groupby("date").ratio.median()
    off = set(h.loc[h.is_holiday.astype(bool), "time_jst"].dt.normalize())
    best = national.idxmax()
    if best in off or best.dayofweek >= 5:
        usual_title = f"Holidays bring up to {(national.max() - 1) * 100:.0f}% more traffic than a usual day"
    else:
        usual_title = f"The busiest day, {best:%b %d}, had {(national.max() - 1) * 100:.0f}% more traffic than usual"

    for name, t in THEMES.items():
        # 1. the shape of a day: workdays against weekends and holidays
        fig, ax = figure(t, 11, 4.6)
        colors = {"Workdays": t["line"], "Weekends and holidays": t["accent"]}
        for label, s in shape.items():
            ax.plot(s.index, s.values, color=colors[label], linewidth=2.4)
        for hr, what in ((am, "morning rush"), (pm, "evening rush")):
            ax.scatter([hr], [wd[hr]], color=t["line"], s=40, zorder=3, edgecolors=t["surface"], linewidths=1.5)
            ax.annotate(f"{what}, {hr}:00–{hr + 1}:00\n{wd[hr]:,.0f} vehicles", (hr, wd[hr]), xytext=(0, 10),
                        textcoords="offset points", color=t["line"], fontsize=11, ha="center", va="bottom")
        mid = (am + pm) / 2
        ax.annotate("Workdays", (mid, wd[int(mid)]), xytext=(0, -14), textcoords="offset points",
                    color=t["line"], fontsize=12, ha="center", va="top")
        band = max(wd.max(), we.max()) * 1.24
        ax.plot([lo, hi], [band, band], color=t["accent"], linewidth=2)
        for x in (lo, hi):
            ax.plot([x, x], [band, band - band * 0.025], color=t["accent"], linewidth=2)
        ax.text((lo + hi) / 2, band * 1.01, f"Weekends and holidays: {we.loc[lo:hi].min():,.0f}–{we[top]:,.0f} vehicles, {lo}:00–{hi + 1}:00", color=t["accent"], fontsize=11, ha="center", va="bottom")
        ax.set_xticks(range(0, 24, 3), [f"{x}:00" for x in range(0, 24, 3)])
        ax.set_xlim(-0.5, 23.5)
        ax.set_ylim(0, max(wd.max(), we.max()) * 1.36)
        ax.set_ylabel("vehicles per hour", color=t["ink2"], fontsize=13)
        title(fig, ax, t, shape_title,
              f"Mean vehicles per hour at a counter (both directions, all sizes), {span}")
        fig.tight_layout()
        fig.savefig(os.path.join(out, f"day_shape_{name}.png"), dpi=110, facecolor=t["surface"])
        plt.close(fig)

        # 2. share of large vehicles by hour
        fig, ax = figure(t, 11, 4.2)
        ax.plot(large.index, large.values, color=t["line"], linewidth=2)
        ax.scatter(large.index, large.values, color=t["line"], s=36, zorder=3, edgecolors=t["surface"], linewidths=1.5)
        peak = large.idxmax()
        ax.set_xticks(range(0, 24, 3), [f"{x}:00" for x in range(0, 24, 3)])
        ax.set_ylabel("large vehicles (%)", color=t["ink2"], fontsize=13)
        ax.set_ylim(0, large.max() * 1.15)
        title(fig, ax, t, f"Large vehicles are {large.max():.0f}% of traffic at {peak}:00 but {large.min():.0f}% at {large.idxmin()}:00",
              f"Share of large vehicles among all counted vehicles, by hour, {span}")
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
        title(fig, ax, t, usual_title,
              "Median over counters of each day's traffic ÷ the same counter's median for that weekday")
        fig.autofmt_xdate(rotation=0, ha="center")
        fig.tight_layout()
        fig.savefig(os.path.join(out, f"vs_usual_{name}.png"), dpi=110, facecolor=t["surface"])
        plt.close(fig)
    print(f"figures through {last} -> {out}")


if __name__ == "__main__":
    main()
