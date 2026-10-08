"""median weekday 24-hour volume (both directions) per counter, September 2026, days with all 24 clean hours"""
import glob
import pandas as pd

hol = set(pd.read_csv("repo/data/holidays_jp.csv").iloc[:, 0].astype(str))
out = []
for f in sorted(glob.glob("b0/hourly_2026-09_*.parquet")):
    d = pd.read_parquet(f)
    flags = [c for c in d.columns if c.endswith(("_failure", "_fault", "_missing"))]
    bad = d[flags].fillna(False).any(axis=1)
    for s in ("up", "down"):
        parts = d[[f"{s}_small", f"{s}_large", f"{s}_unclassified"]].astype("float")
        d[s] = d[f"{s}_total"].astype("float").fillna(parts.sum(axis=1, min_count=2))
    d["v"] = d.up + d.down
    d["large"] = d.up_large.astype(float) + d.down_large.astype(float)
    d = d[~bad & d.v.notna()]
    d["day"] = d.time_jst.dt.normalize()
    h = d.time_jst.dt.hour
    d["v12"] = d.v.where((h >= 7) & (h < 19), 0)
    d["l12"] = d.large.where((h >= 7) & (h < 19), 0)
    g = d.groupby(["counter_id", "sensor", "day"]).agg(v=("v", "sum"), large=("large", "sum"), v12=("v12", "sum"),
                                                       l12=("l12", "sum"), up=("up", "sum"), n=("v", "size")).reset_index()
    out.append(g[g.n == 24])
g = pd.concat(out)
g = g[(g.day.dt.dayofweek < 5) & ~g.day.dt.strftime("%Y-%m-%d").isin(hol)]
r = g.groupby(["counter_id", "sensor"]).agg(vol24=("v", "median"), large24=("large", "median"), v12=("v12", "sum"), l12=("l12", "sum"), vsum=("v", "sum"), upsum=("up", "sum"), days=("v", "size")).reset_index()
r["large12_pct"] = 100 * r.l12 / r.v12
r["daynight"] = r.vsum / r.v12
r["up_pct"] = 100 * r.upsum / r.vsum
r = r[r.days >= 5].drop(columns=["v12", "l12", "vsum", "upsum"])
r.to_csv("census/counter_vol.csv", index=False)
print(len(r), r.vol24.describe())
