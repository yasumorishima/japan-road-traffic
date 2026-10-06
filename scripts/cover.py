"""Cover image from the data: every counter of one day as a point of light at its location, brightness and
size by that day's vehicle count (both directions, all sizes).

  python scripts/cover.py <hourly parquet> <counters.csv> <out.jpg>"""
import sys
import numpy as np
import pandas as pd
from PIL import Image, ImageFilter

src, counters, out = sys.argv[1:4]
W, H, S = 1456, 720, 2
w, h = W * S, H * S

df = pd.read_parquet(src)
day = df.time_jst.dt.date.value_counts().idxmax()  # the best-covered day
df = df[df.time_jst.dt.date == day]
parts = ["up_small", "up_large", "up_unclassified", "down_small", "down_large", "down_unclassified"]
loop = df.sensor == "loop"
df["n"] = np.where(loop, df[parts].fillna(0).sum(axis=1), df[["up_total", "down_total"]].fillna(0).sum(axis=1))
vol = df.groupby(["counter_id", "sensor"]).n.sum().rename("vol").reset_index()
c = pd.read_csv(counters).merge(vol, on=["counter_id", "sensor"])
c = c[(c.vol > 0) & c.longitude.between(128.5, 146.2) & c.latitude.between(30.8, 45.6)]

# equirectangular with the scale of latitude 37, fitted to the height
lat0 = np.cos(np.radians(37.0))
lon_c, lat_c = 137.4, 38.2
k = 0.94 * h / (45.6 - 30.8)
x = w / 2 + (c.longitude - lon_c) * lat0 * k
y = h / 2 - (c.latitude - lat_c) * k
v = np.log1p(c.vol.values)
v = (v - v.min()) / (v.max() - v.min())

glow = np.zeros((h, w, 3))
yy, xx = np.mgrid[-16:17, -16:17]
warm = np.array([255, 170, 60]); cool = np.array([90, 170, 255])
for xi, yi, vi in zip(x.values, y.values, v):
    r = 1.6 + 3.4 * vi ** 2
    spot = np.exp(-(xx ** 2 + yy ** 2) / (2 * r ** 2))[..., None] * (0.15 + 0.85 * vi ** 1.5)
    col = cool + (warm - cool) * vi
    xi, yi = int(xi), int(yi)
    x0, x1, y0, y1 = xi - 16, xi + 17, yi - 16, yi + 17
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
        continue
    glow[y0:y1, x0:x1] += spot * col / 255
img = np.clip(glow, 0, 1)
bg = np.array([8, 12, 24]) / 255
img = bg + (1 - bg) * (1 - np.exp(-1.1 * img))  # soft saturation where points overlap
im = Image.fromarray((img * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6)).resize((W, H), Image.LANCZOS)
im.save(out, quality=92)
print(f"{day}: {len(c)} counters drawn -> {out}")
