"""Animated maps for the README (GIF, dark background), drawn from the Kaggle build.

  python scripts/animate.py <build dir> <land json> <out dir>

  day_japan.gif     a workday, hour by hour: every counter in Japan as a point of light, brightness by vehicles
  day_kanto.gif     the same for the Kanto box, every 15 minutes, from the five-minute files
  busier_<name>.gif a holiday period, every two hours: counters carrying more than usual glow warm. Drawn once,
                    when the build covers the period and the two weeks before it, and kept after that (delete the
                    file to redraw it). Add new holiday periods to PERIODS.

Rows the source flags as faulty or missing are dropped (`vehicles` is empty for them). The land is Natural Earth
(public domain)."""
import glob, json, os, sys
import numpy as np
import pandas as pd
import matplotlib
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT = os.path.join(matplotlib.get_data_path(), "fonts", "ttf", "DejaVuSans.ttf")
BOLD = os.path.join(matplotlib.get_data_path(), "fonts", "ttf", "DejaVuSans-Bold.ttf")
BG = (12, 16, 28)
LAND = (30, 36, 52)
INK = (240, 240, 236)
INK2 = (170, 172, 180)
COOL, WARM = np.array([90, 170, 255]), np.array([255, 170, 60])
# periods with many holidays; each is drawn only when the build covers it
PERIODS = {"obon": ("2026-08-07 12:00", "2026-08-17 00:00", "Obon holidays"),
           "silver_week": ("2026-09-18 12:00", "2026-09-24 00:00", "Silver Week holidays")}


def font(size, bold=False):
    return ImageFont.truetype(BOLD if bold else FONT, size)


class Map:
    """Equirectangular projection at the scale of the box's middle latitude, fitted into w x h."""

    def __init__(self, box, w, h, top, bottom, rings):
        self.x0, self.x1, self.y0, self.y1 = box
        self.w, self.h, self.top = w, h, top
        k = np.cos(np.radians((self.y0 + self.y1) / 2))
        self.s = min(w / ((self.x1 - self.x0) * k), (h - top - bottom) / (self.y1 - self.y0))
        self.k = k
        self.cx = (w - (self.x1 - self.x0) * k * self.s) / 2
        base = Image.new("RGB", (w, h), BG)
        d = ImageDraw.Draw(base)
        for r in rings:
            r = np.asarray(r)
            if r[:, 0].max() < self.x0 - 1 or r[:, 0].min() > self.x1 + 1 or \
                    r[:, 1].max() < self.y0 - 1 or r[:, 1].min() > self.y1 + 1:
                continue
            x, y = self.xy(r[:, 0], r[:, 1])
            d.polygon(list(zip(x.tolist(), y.tolist())), fill=LAND)
        # keep the land inside the map area only
        d.rectangle([0, 0, w, top - 1], fill=BG)
        d.rectangle([0, h - bottom, w, h], fill=BG)
        self.base = np.asarray(base, dtype=np.float64) / 255

    def xy(self, lon, lat):
        return (self.cx + (np.asarray(lon) - self.x0) * self.k * self.s,
                self.top + (self.y1 - np.asarray(lat)) * self.s)

    def glow(self, lon, lat, v, color, radius):
        """Points of light: v in 0..1 sets size and brightness; color is one RGB per point (0..255)."""
        out = np.zeros((self.h, self.w, 3))
        R = int(np.ceil(radius * 5))
        yy, xx = np.mgrid[-R:R + 1, -R:R + 1]
        x, y = self.xy(lon, lat)
        for xi, yi, vi, ci in zip(x, y, v, color):
            if vi <= 0:
                continue
            r = radius * (0.45 + 0.75 * vi)
            spot = np.exp(-(xx ** 2 + yy ** 2) / (2 * r ** 2))[..., None] * (0.12 + 0.88 * vi ** 1.4)
            xi, yi = int(round(xi)), int(round(yi))
            x0, x1, y0, y1 = xi - R, xi + R + 1, yi - R, yi + R + 1
            if x0 < 0 or y0 < 0 or x1 > self.w or y1 > self.h:
                continue
            out[y0:y1, x0:x1] += spot * ci / 255
        img = self.base + (1 - self.base) * (1 - np.exp(-1.2 * out))
        return Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))


def strip(d, box, series, now, color, label, ref=None, ticks=(), span=None):
    """A small line chart of `series` (index 0..n-1) with a marker at `now`."""
    x0, y0, x1, y1 = box
    v = np.asarray(series, dtype=float)
    if span:
        v = np.clip(v, *span)
    if span:
        lo, hi = span
    else:
        lo, hi = np.nanmin(v), np.nanmax(v)
        pad = (hi - lo) * 0.1 or 1
        lo, hi = lo - pad, hi + pad
    px = lambda i: x0 + (x1 - x0) * i / (len(v) - 1)
    py = lambda val: y1 - (y1 - y0) * (val - lo) / (hi - lo)
    if ref is not None:
        d.line([(x0, py(ref)), (x1, py(ref))], fill=(70, 74, 88), width=1)
    pts = [(i, (px(i), py(val))) for i, val in enumerate(v) if np.isfinite(val)]
    d.line([p for _, p in pts], fill=(110, 116, 130), width=2)
    past = [p for i, p in pts if i <= now]
    if len(past) > 1:
        d.line(past, fill=color, width=3)
    cx, cy = px(now), py(v[now])
    d.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=color)
    d.text((x0, y0 - 6), label, font=font(15), fill=INK2, anchor="ls")
    for i, text in ticks:
        d.line([(px(i), y1 + 2), (px(i), y1 + 7)], fill=INK2, width=1)
        d.text((px(i), y1 + 9), text, font=font(13), fill=INK2, anchor="mt")


def save_gif(frames, path, ms, hold_last=0):
    pal = frames[len(frames) // 2].quantize(colors=96, method=Image.Quantize.MEDIANCUT)
    q = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    durations = [ms] * len(q)
    durations[-1] += hold_last
    q[0].save(path, save_all=True, append_images=q[1:], duration=durations, loop=0, optimize=True)
    print(f"{path}: {len(q)} frames, {os.path.getsize(path) / 1e6:.2f} MB, {sum(durations) / 1000:.1f} s")


def load(build, pattern, extra=()):
    files = sorted(glob.glob(os.path.join(build, pattern)))
    if not files:
        sys.exit(f"no {pattern} in the build")
    cols = ["time_jst", "counter_id", "sensor", "vehicles", "is_holiday", *extra]
    df = pd.concat([pd.read_parquet(p, columns=cols) for p in files], ignore_index=True)
    df = df[df.vehicles.notna()].copy()
    df["vehicles"] = df.vehicles.astype("float64")
    df["workday"] = (df.time_jst.dt.dayofweek < 5) & ~df.is_holiday.astype(bool)
    return df


def where(df, counters):
    c = counters[["counter_id", "sensor", "longitude", "latitude"]]
    return df.merge(c, on=["counter_id", "sensor"], how="inner")


def scale(v, hi):
    """square-root scale to 0..1 against one fixed vehicle count for every frame, so quiet hours stay dim"""
    return np.clip(np.sqrt(np.maximum(v, 0) / hi), 0, 1)


def header(d, w, title, sub, now, extra=""):
    d.text((28, 36), title, font=font(24, True), fill=INK, anchor="ls")
    d.text((28, 62), sub, font=font(15), fill=INK2, anchor="ls")
    d.text((28, 112), now, font=font(40, True), fill=INK, anchor="ls")
    if extra:
        d.text((w - 28, 108), extra, font=font(18), fill=INK2, anchor="rs")


def day_map(prof, m, steps, labels, total, title, sub, path, ms, radius, places=(), ticks=()):
    """prof: rows counter x step with mean vehicles per hour; one frame per step."""
    hi = np.nanpercentile(prof.values, 99)
    frames = []
    for i, step in enumerate(steps):
        v = scale(prof[step].fillna(0).values, hi)
        col = COOL + (WARM - COOL) * v[:, None]
        im = m.glow(prof.index.get_level_values("longitude"), prof.index.get_level_values("latitude"), v, col, radius)
        d = ImageDraw.Draw(im)
        for name, lon, lat in places:
            x, y = m.xy(lon, lat)
            d.text((float(x) + 9, float(y)), name, font=font(16), fill=INK2, anchor="lm")
        header(d, m.w, title, sub, labels[i])
        strip(d, (60, m.h - 95, m.w - 60, m.h - 40), total.values, i, tuple(WARM), "vehicles, all counters together",
              ticks=ticks)
        frames.append(im)
    save_gif(frames, path, ms, hold_last=800)


def busier(h, counters, m, start, end, name, label, path):
    """Each counter's vehicles ÷ its usual for that hour on the same kind of day (workday or weekend/holiday),
    the usual being the median over the days outside every holiday period with at least five such days."""
    t0, t1 = pd.Timestamp(start), pd.Timestamp(end)
    h = h.assign(hour=h.time_jst.dt.hour, date=h.time_jst.dt.normalize())
    outside = pd.Series(True, index=h.index)
    for s, e, _ in PERIODS.values():
        outside &= ~h.time_jst.between(pd.Timestamp(s).normalize() - pd.Timedelta(days=1),
                                       pd.Timestamp(e) + pd.Timedelta(days=1))
    ref = h[outside]
    usual = ref.groupby(["counter_id", "sensor", "workday", "hour"]).vehicles.agg(["median", "size"])
    usual = usual[(usual["size"] >= 5) & (usual["median"] >= 20)]["median"].rename("usual")
    win = h[(h.time_jst >= t0) & (h.time_jst < t1)]
    win = win.join(usual, on=["counter_id", "sensor", "workday", "hour"], how="inner")
    win["ratio"] = win.vehicles / win.usual
    win = where(win, counters)
    times = pd.date_range(t0, t1 - pd.Timedelta(hours=2), freq="2h")
    # the strip: each day, all counters together, vehicles ÷ usual (one value per day, drawn as steps over its hours)
    g = win.groupby(win.time_jst.dt.normalize())
    day = g.vehicles.sum() / g.usual.sum()
    hours = pd.date_range(t0, t1, freq="1h", inclusive="left")
    nat = pd.Series(day.reindex(hours.normalize()).values, index=hours)
    print(name, "each day, all counters together ÷ usual:", " ".join(f"{d:%m-%d} {r:.3f}" for d, r in day.items()))
    ticks = [(nat.index.get_loc(t), f"{t:%b %d}") for t in nat.index if t.hour == 0]
    frames = []
    for i, ts in enumerate(times):
        f = win[win.time_jst == ts]
        r = f.ratio.values
        # warm and bright above usual (×1.0 → ×2.0), faint and cool below it
        up = np.clip(np.log(np.maximum(r, 1e-6)) / np.log(2.0), 0, 1)
        v = np.where(r > 1.1, 0.15 + 0.85 * up, 0.10)
        col = np.where((r > 1.1)[:, None], WARM[None, :] + (np.array([255, 90, 50]) - WARM)[None, :] * up[:, None],
                       np.array([70, 110, 170])[None, :])
        im = m.glow(f.longitude.values, f.latitude.values, v, col, 2.2)
        d = ImageDraw.Draw(im)
        header(d, m.w, f"Busier than usual: {label}",
               "each counter ÷ its usual for that hour and kind of day; warm = more than 10% above usual",
               f"{ts:%a %b %d}  {ts:%H}:00", f"{(r > 1.1).mean() * 100:.0f}% of counters warm")
        strip(d, (60, m.h - 95, m.w - 60, m.h - 40), nat.values, nat.index.get_loc(ts), (255, 140, 60),
              f"each day, all counters together ÷ usual (grey line: usual; now ×{nat[ts]:.2f})", ref=1.0, ticks=ticks,
              span=(0.9, 1.2))
        frames.append(im)
    save_gif(frames, path, 220, hold_last=1200)


def main():
    build, land, out = sys.argv[1:4]
    os.makedirs(out, exist_ok=True)
    rings = json.load(open(land))["rings"]
    counters = pd.read_csv(os.path.join(build, "counters.csv"))

    h = load(build, "hourly_*.parquet")
    span = f"{h.time_jst.min():%Y-%m-%d} to {h.time_jst.max():%Y-%m-%d}"
    wd = where(h[h.workday], counters).assign(hour=lambda x: x.time_jst.dt.hour)
    wd = wd[wd.longitude.between(128.5, 146.2) & wd.latitude.between(30.8, 45.8)]
    prof = wd.groupby(["counter_id", "sensor", "longitude", "latitude", "hour"]).vehicles.mean().unstack("hour")
    prof = prof.reindex(columns=range(24))
    total = prof.sum().reindex(range(24))
    m = Map((128.5, 146.2, 30.8, 45.8), 900, 1040, 130, 120, rings)
    day_map(prof, m, list(range(24)), [f"{x:02d}:00" for x in range(24)], total,
            "A workday on Japan's national roads", f"mean vehicles per hour at each counter, workdays {span}",
            os.path.join(out, "day_japan.gif"), 420, 2.0,
            ticks=[(x, f"{x}:00") for x in (0, 6, 12, 18, 23)])

    f = load(build, "five_minute_kanto_*.parquet")
    box = (138.95, 140.45, 35.1, 36.35)  # Tokyo, Yokohama, Saitama and Chiba inside the five-minute box
    f = where(f[f.workday], counters)
    f = f[f.longitude.between(box[0], box[1]) & f.latitude.between(box[2], box[3])]
    f["slot"] = f.time_jst.dt.hour * 4 + f.time_jst.dt.minute // 15
    # mean vehicles per five minutes in each quarter hour, ×12 = per hour
    kp = (f.groupby(["counter_id", "sensor", "longitude", "latitude", "slot"]).vehicles.mean() * 12).unstack("slot")
    kp = kp.reindex(columns=range(96))
    km = Map(box, 900, 1040, 130, 120, rings)
    places = [("Tokyo", 139.767, 35.681), ("Yokohama", 139.638, 35.444), ("Saitama", 139.645, 35.861),
              ("Chiba", 140.123, 35.605), ("Hachioji", 139.316, 35.656), ("Kumagaya", 139.388, 36.147)]
    kspan = f"{f.time_jst.min():%Y-%m-%d} to {f.time_jst.max():%Y-%m-%d}"
    day_map(kp, km, list(range(96)), [f"{s // 4:02d}:{s % 4 * 15:02d}" for s in range(96)], kp.sum(),
            "A workday around Tokyo, every 15 minutes", f"mean vehicles per hour at each counter, workdays {kspan}",
            os.path.join(out, "day_kanto.gif"), 110, 4.5, places,
            ticks=[(x * 4, f"{x}:00") for x in (0, 6, 12, 18)] + [(95, "23:45")])

    for name, (s, e, label) in PERIODS.items():
        path = os.path.join(out, f"busier_{name}.gif")
        if os.path.exists(path):
            print(f"{path}: kept")
        elif h.time_jst.min() <= pd.Timestamp(s) - pd.Timedelta(days=14) and h.time_jst.max() >= pd.Timestamp(e):
            busier(h, counters, m, s, e, name, label, path)


if __name__ == "__main__":
    main()
