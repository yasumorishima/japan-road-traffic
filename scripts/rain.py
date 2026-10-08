"""Traffic in the rain, for the README: a chart (light and dark) and an animated map of the rainiest day.

  python scripts/rain.py <build dir> <amedas dir> <land json> <out dir>

<amedas dir> holds the files of the amedas-* releases (amedas_YYYYMMDD.csv.gz and amedas_stations_YYYYMM.csv.gz).

Each permanent counter takes the rain of its nearest AMeDAS station within 10 km (rain in the hour, precip_1h_mm,
only values with the normal quality flag). A counter's hour is compared with its usual: the median of the same
counter, hour and kind of day (workday, or weekend and holiday) over the days outside the holiday periods (at least
five days, median at least 20 vehicles). Rain falls on some days more than others and on some regions more than
others, so a rainy counter is compared with the dry counters of the same hour: rel = (vehicles / usual) / (median of
vehicles / usual over the counters with no rain that hour). rel below 1 means fewer vehicles than the dry counters
had at the same time.

  rain_effect_<theme>.png  median rel by the rain in the hour, workdays and weekends/holidays
  rain_day.gif             the day with the most rainy counter-hours: rain as blue light at the stations, counters
                           under rain coloured by rel, and below the map each hour's median rel under rain"""
import glob, json, os, sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import ImageDraw

sys.path.insert(0, os.path.dirname(__file__))
import animate, figures

NEAR_KM = 10
WET = 1.0            # mm in the hour that counts as rain for the headline numbers
MIN_HOURS = 100      # counter-hours a rain class needs to be drawn
BINS = [(0.5, 0.5, "0.5"), (1.0, 1.0, "1"), (1.5, 3.0, "1.5–3"), (3.5, 5.0, "3.5–5"), (5.5, 10.0, "5.5–10"),
        (10.5, np.inf, "over 10")]


def usual(h):
    """median vehicles of the same counter, hour and kind of day, outside every holiday period (±1 day)"""
    outside = pd.Series(True, index=h.index)
    for s, e, _ in animate.PERIODS.values():
        outside &= ~h.time_jst.between(pd.Timestamp(s).normalize() - pd.Timedelta(days=1),
                                       pd.Timestamp(e) + pd.Timedelta(days=1))
    u = h[outside].groupby(["counter_id", "sensor", "workday", "hour"]).vehicles.agg(["median", "size"])
    return u[(u["size"] >= 5) & (u["median"] >= 20)]["median"].rename("usual")


def nearest(counters, stations):
    la1, lo1 = np.radians(counters.latitude.values)[:, None], np.radians(counters.longitude.values)[:, None]
    la2, lo2 = np.radians(stations.lat.values)[None, :], np.radians(stations.lon.values)[None, :]
    a = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    d = 6371 * 2 * np.arcsin(np.sqrt(a))
    out = counters[["counter_id", "sensor", "longitude", "latitude"]].copy()
    out["station_id"] = stations.station_id.values[d.argmin(1)]
    out["km"] = d.min(1)
    return out[out.km <= NEAR_KM]


def load_weather(wdir):
    files = sorted(glob.glob(os.path.join(wdir, "amedas_2*.csv.gz")))
    st_files = sorted(glob.glob(os.path.join(wdir, "amedas_stations_*.csv.gz")))
    if not files or not st_files:
        sys.exit("no AMeDAS files")
    w = pd.concat([pd.read_csv(f, dtype={"station_id": str, "hour_start_jst": str},
                               usecols=["hour_start_jst", "station_id", "precip_1h_mm", "precip_1h_mm_aqc"])
                   for f in files], ignore_index=True)
    w = w[(w.precip_1h_mm_aqc == 0) & w.precip_1h_mm.notna()]
    w["time_jst"] = pd.to_datetime(w.hour_start_jst, format="%Y%m%d%H%M")
    stations = pd.read_csv(st_files[-1], dtype={"station_id": str})
    return w[["time_jst", "station_id", "precip_1h_mm"]], stations


def main():
    build, wdir, land, out = sys.argv[1:5]
    os.makedirs(out, exist_ok=True)
    w, stations = load_weather(wdir)
    counters = pd.read_csv(os.path.join(build, "counters.csv"))
    h = animate.load(build, "hourly_*.parquet")
    h = h[h.sensor == "loop"].assign(hour=lambda x: x.time_jst.dt.hour)
    h = h.join(usual(h), on=["counter_id", "sensor", "workday", "hour"], how="inner")
    near = nearest(counters[counters.sensor == "loop"], stations)
    x = h[h.time_jst.between(w.time_jst.min(), w.time_jst.max())].merge(near, on=["counter_id", "sensor"])
    x = x.merge(w, on=["time_jst", "station_id"])
    x["ratio"] = x.vehicles / x.usual
    dry = x[x.precip_1h_mm == 0].groupby("time_jst").ratio.agg(["median", "size"])
    dry = dry[dry["size"] >= 100]["median"].rename("dry")
    x = x.join(dry, on="time_jst", how="inner")
    x["rel"] = x.ratio / x.dry
    wet = x[x.precip_1h_mm >= WET]
    days = x.time_jst.dt.normalize().nunique()
    span = f"{x.time_jst.min():%Y-%m-%d} to {x.time_jst.max():%Y-%m-%d}"
    print(f"{len(x):,} counter-hours over {days} days, {len(wet):,} with rain ≥ {WET} mm, "
          f"{x.counter_id.nunique()} counters with a station within {NEAR_KM} km")
    if len(wet) < 1000:
        sys.exit("too little rain to draw")

    # 1. the chart
    rows = []
    for work, label in ((True, "Workdays"), (False, "Weekends and holidays")):
        g = x[x.workday == work]
        for lo, hi, name in BINS:
            s = g[(g.precip_1h_mm >= lo) & (g.precip_1h_mm <= hi)].rel
            rows.append((label, name, (s.median() - 1) * 100 if len(s) >= MIN_HOURS else np.nan, len(s)))
    tab = pd.DataFrame(rows, columns=["kind", "rain", "pct", "n"])
    keep = tab.groupby("rain", sort=False).pct.apply(lambda s: s.notna().any())
    tab = tab[tab.rain.map(keep)]
    names = list(dict.fromkeys(tab.rain))
    print(tab.to_string(index=False))
    head = {k: (wet[wet.workday == (k == "Workdays")].rel.median() - 1) * 100 for k in ("Workdays", "Weekends and holidays")}
    a, b = head["Workdays"], head["Weekends and holidays"]
    if a < 0 and b < 0:
        main_title = (f"In the rain, traffic drops {-a:.0f}% on workdays and {-b:.0f}% on weekends and holidays"
                      if round(a) != round(b) else f"In the rain, traffic drops {-a:.0f}%")
    else:
        main_title = "Traffic in the rain compared with dry roads at the same hour"
    for name, t in figures.THEMES.items():
        fig, ax = figures.figure(t, 11, 4.6)
        ax.axhline(0, color=t["base"], linewidth=1.2)
        colors = {"Workdays": t["line"], "Weekends and holidays": t["accent"]}
        for kind, g in tab.groupby("kind", sort=False):
            xs = np.arange(len(g))
            ax.plot(xs, g.pct.values, color=colors[kind], linewidth=2.4, marker="o", markersize=6,
                    markeredgecolor=t["surface"], markeredgewidth=1.5)
            last = int(np.nanargmax(np.where(np.isfinite(g.pct.values), xs, -1)))
            ax.annotate(kind, (last, g.pct.values[last]), xytext=(8, 0), textcoords="offset points",
                        color=colors[kind], fontsize=12, va="center")
        ax.set_xticks(range(len(names)), names)
        ax.set_xlim(-0.3, len(names) - 1 + 1.9)
        ax.set_xlabel("rain in the hour at the nearest AMeDAS station (mm)", color=t["ink2"], fontsize=13)
        ax.set_ylabel("vehicles vs dry roads (%)", color=t["ink2"], fontsize=13)
        lo = min(-12, np.nanmin(tab.pct) - 2)
        ax.set_ylim(lo, 2)
        ax.text(-0.25, 0.3, "dry roads at the same hour", color=t["muted"], fontsize=11, va="bottom")
        figures.title(fig, ax, t, main_title,
                      f"Each counter vs its usual day, then vs dry counters at the same hour; {span}, "
                      f"{len(wet):,} rainy counter-hours")
        fig.tight_layout()
        fig.savefig(os.path.join(out, f"rain_effect_{name}.png"), dpi=110, facecolor=t["surface"])
        plt.close(fig)

    # 2. the rainiest day as an animated map
    # the day with the most rainy counter-hours among the days with all 24 maps
    full = w.groupby(w.time_jst.dt.normalize()).time_jst.nunique()
    counts = wet.time_jst.dt.normalize().value_counts()
    day = counts[counts.index.isin(full[full == 24].index)].idxmax()
    rings = json.load(open(land))["rings"]
    m = animate.Map((128.5, 146.2, 30.8, 45.8), 900, 1040, 130, 120, rings)
    hours = pd.date_range(day, day + pd.Timedelta(hours=23), freq="1h")
    per = wet.groupby("time_jst").rel.agg(["median", "size"]).reindex(hours)
    per.loc[per["size"] < 20, "median"] = np.nan
    strip_v = (per["median"] - 1) * 100
    wst = w.merge(stations[["station_id", "lat", "lon"]], on="station_id")
    frames = []
    for i, ts in enumerate(hours):
        r = wst[(wst.time_jst == ts) & (wst.precip_1h_mm >= 0.5)]
        v = np.clip(np.sqrt(r.precip_1h_mm.values / 20), 0.15, 1)
        im = m.glow(r.lon.values, r.lat.values, v * 0.8, np.tile([50, 100, 255], (len(r), 1)), 7.0)
        c = x[(x.time_jst == ts) & (x.precip_1h_mm >= WET)]
        if len(c):
            # counters under rain: cool when below dry roads, warm when above
            below = c.rel.values < 1
            col = np.where(below[:, None], np.array([235, 245, 255])[None, :], animate.WARM[None, :])
            layer = m.glow(c.longitude.values, c.latitude.values, np.full(len(c), 0.9), col, 2.2)
            im = layer if not len(r) else _screen(im, layer, m)
        d = ImageDraw.Draw(im)
        n = len(c)
        share = (c.rel < 1).mean() * 100 if n else np.nan
        animate.header(d, m.w, "Rain and traffic", "blue: rain    white: a counter in the rain, below dry roads    orange: above them", f"{ts:%a %b %d}  {ts:%H}:00",
                       f"{n} counters under rain" + (f", {share:.0f}% below dry roads" if n >= 20 else ""))
        rain_strip(d, (60, m.h - 95, m.w - 60, m.h - 40), strip_v.values, i)
        frames.append(im)
    animate.save_gif(frames, os.path.join(out, "rain_day.gif"), 450, hold_last=1200)


def rain_strip(d, box, vals, now):
    """each hour's median of counters under rain vs dry roads, in %; hours with fewer than 20 such counters blank"""
    x0, y0, x1, y1 = box
    lo, hi = -15.0, 5.0
    px = lambda i: x0 + (x1 - x0) * i / (len(vals) - 1)
    py = lambda v: y1 - (y1 - y0) * (np.clip(v, lo, hi) - lo) / (hi - lo)
    d.line([(x0, py(0)), (x1, py(0))], fill=(70, 74, 88), width=1)
    d.text((x1, py(0) - 4), "dry roads", font=animate.font(13), fill=animate.INK2, anchor="rs")
    color, grey = (235, 245, 255), (110, 116, 130)
    for i in range(len(vals) - 1):
        if np.isfinite(vals[i]) and np.isfinite(vals[i + 1]):
            d.line([(px(i), py(vals[i])), (px(i + 1), py(vals[i + 1]))], fill=color if i + 1 <= now else grey, width=3)
    for i, v in enumerate(vals):
        if np.isfinite(v) and i <= now:
            d.ellipse([px(i) - 2, py(v) - 2, px(i) + 2, py(v) + 2], fill=color)
    if np.isfinite(vals[now]):
        d.ellipse([px(now) - 5, py(vals[now]) - 5, px(now) + 5, py(vals[now]) + 5], fill=color)
        label = f"counters under rain vs dry roads that hour: {vals[now]:+.0f}%"
    else:
        d.line([(px(now), y0), (px(now), y1)], fill=grey, width=1)
        label = "counters under rain vs dry roads that hour (too few under rain now)"
    d.text((x0, y0 - 6), label, font=animate.font(15), fill=animate.INK2, anchor="ls")
    for k in (0, 6, 12, 18, 23):
        d.line([(px(k), y1 + 2), (px(k), y1 + 7)], fill=animate.INK2, width=1)
        d.text((px(k), y1 + 9), f"{k}:00", font=animate.font(13), fill=animate.INK2, anchor="mt")


def _screen(a, b, m):
    """combine two glow images over the same base: add their light above the base"""
    base = m.base
    fa, fb = np.asarray(a, dtype=np.float64) / 255, np.asarray(b, dtype=np.float64) / 255
    la, lb = np.clip((fa - base) / np.maximum(1 - base, 1e-6), 0, 1), np.clip((fb - base) / np.maximum(1 - base, 1e-6), 0, 1)
    light = 1 - (1 - la) * (1 - lb)
    from PIL import Image
    return Image.fromarray((np.clip(base + (1 - base) * light, 0, 1) * 255).astype(np.uint8))


if __name__ == "__main__":
    main()
