"""Cover image from the data: every counter of one day as a point of light at its location, brightness and
size by that day's vehicle count (both directions, all sizes). All of Japan on the left, and two close-ups on the
right (Tokyo and the Osaka-Kyoto-Kobe area), on the same brightness scale. The land is Natural Earth (public domain).

  python scripts/cover.py <hourly parquet> <counters.csv> <land json> <out.jpg>"""
import json, os, sys
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from animate import BG, COOL, INK, INK2, WARM, Map, font  # noqa: E402

src, counters, land, out = sys.argv[1:5]
W, H = 1456, 728  # 2:1, the shape Kaggle crops a dataset cover to
JAPAN = (128.5, 146.2, 30.8, 45.6)
# close-ups: centre (lon, lat), latitude span, title, short name on the map of Japan, cities to name
CLOSE = [((139.7, 35.6), 0.9, "Tokyo area", "Tokyo",
          [("Tokyo", 139.767, 35.681), ("Yokohama", 139.638, 35.444), ("Saitama", 139.645, 35.861),
           ("Chiba", 140.123, 35.607)]),
         ((135.5, 34.75), 0.8, "Osaka, Kyoto and Kobe", "Osaka",
          [("Osaka", 135.502, 34.694), ("Kyoto", 135.768, 35.012), ("Kobe", 135.195, 34.690),
           ("Nara", 135.805, 34.685)])]

df = pd.read_parquet(src)
day = df.time_jst.dt.date.value_counts().idxmax()  # the best-covered day
df = df[df.time_jst.dt.date == day]
parts = ["up_small", "up_large", "up_unclassified", "down_small", "down_large", "down_unclassified"]
loop = df.sensor == "loop"
df["n"] = np.where(loop, df[parts].fillna(0).sum(axis=1), df[["up_total", "down_total"]].fillna(0).sum(axis=1))
vol = df.groupby(["counter_id", "sensor"]).n.sum().rename("vol").reset_index()
c = pd.read_csv(counters).merge(vol, on=["counter_id", "sensor"])
c = c[(c.vol > 0) & c.longitude.between(JAPAN[0], JAPAN[1]) & c.latitude.between(JAPAN[2], JAPAN[3])]
v = np.log1p(c.vol.values)
v = (v - v.min()) / (v.max() - v.min())  # one scale for all three panels
col = COOL + (WARM - COOL) * v[:, None]
rings = json.load(open(land))["rings"]

canvas = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(canvas)
d.text((28, 40), "One day of traffic, one point of light per counter", font=font(24, True), fill=INK, anchor="ls")
d.text((28, 66), f"{day:%Y-%m-%d}, {len(c):,} counters; brighter and warmer = more vehicles that day",
       font=font(15), fill=INK2, anchor="ls")

# all of Japan
LW, top = 800, 84
m = Map(JAPAN, LW, H - top, 1, 10, rings)
canvas.paste(m.glow(c.longitude, c.latitude, v, col, 2.2), (0, top))

# the close-ups, stacked on the right, each marked on the map of Japan
x0, PW, gap = LW + 12, W - LW - 12 - 28, 28
PH = (H - top - 10 - gap) // 2
for i, ((lon, lat), span, label, short, cities) in enumerate(CLOSE):
    y0, ph = top + i * (PH + gap) + 26, PH - 26
    # widen the longitude span so that the box has the panel's shape and fills it
    half = span / 2 * PW / ph / np.cos(np.radians(lat))
    box = (lon - half, lon + half, lat - span / 2, lat + span / 2)
    z = Map(box, PW, ph, 1, 1, rings)
    # Map.glow skips a point whose spot would cross the panel edge, so count only the points it draws
    R = int(np.ceil(3.0 * 5))
    px, py = z.xy(c.longitude.values, c.latitude.values)
    px, py = np.round(px), np.round(py)
    inside = pd.Series((px - R >= 0) & (py - R >= 0) & (px + R + 1 <= PW) & (py + R + 1 <= ph), index=c.index)
    im = z.glow(c.longitude[inside], c.latitude[inside], v[inside.values], col[inside.values], 3.0)
    dz = ImageDraw.Draw(im)
    for name, clon, clat in cities:
        cx, cy = z.xy(clon, clat)
        dz.text((float(cx), float(cy)), name, font=font(15), fill=INK2, anchor="mm", stroke_width=3,
                stroke_fill=BG)
    dz.rectangle([0, 0, PW - 1, ph - 1], outline=INK2, width=1)
    canvas.paste(im, (x0, y0))
    d.text((x0, y0 - 8), f"{label}: {int(inside.sum())} counters", font=font(16, True), fill=INK, anchor="ls")
    mx0, my0 = m.xy(box[0], box[3])
    mx1, my1 = m.xy(box[1], box[2])
    d.rectangle([mx0, top + my0, mx1, top + my1], outline=INK, width=2)
    d.text((mx1 + 6, top + (my0 + my1) / 2), short, font=font(15), fill=INK, anchor="lm")
canvas.save(out, quality=92)
print(f"{day}: {len(c)} counters drawn -> {out}")
