"""my own recomputation of the audit's two independent checks: daytime peak-hour share (census col 63) and the
up-minus-down large-vehicle share (census up 12h cols 38-40, down 50-52)"""
import csv, glob, io
import numpy as np, pandas as pd
O = pd.read_csv("census/census_r3_match.csv", dtype={"census_section": str})
M = pd.read_csv("census/match_perm.csv", dtype={"section": str}).set_index("counter_id")
O["lr"] = O.counter_id.map(M.lr)
hol = set(pd.read_csv("repo/data/holidays_jp.csv").iloc[:, 0].astype(str))
parts = []
for f in sorted(glob.glob("b0/hourly_2026-09_*.parquet")):
    d = pd.read_parquet(f)
    d = d[(d.sensor == "loop") & d.counter_id.isin(O.counter_id)]
    flags = [c for c in d.columns if c.endswith(("_failure", "_fault", "_missing"))]
    d = d[~d[flags].fillna(False).any(axis=1)]
    h = d.time_jst.dt.hour
    d = d[(h >= 7) & (h < 19) & (d.time_jst.dt.dayofweek < 5) & ~d.time_jst.dt.strftime("%Y-%m-%d").isin(hol)]
    for s in ("up", "down"):
        pp = d[[f"{s}_small", f"{s}_large", f"{s}_unclassified"]].astype("float")
        d[s + "_v"] = d[s + "_total"].astype(float).fillna(pp.sum(axis=1, min_count=2))  # same as daily_vol.py
        d[s + "_l"] = d[s + "_large"].astype(float)
    d["v"] = d.up_v + d.down_v
    d["day"] = d.time_jst.dt.normalize()
    parts.append(d[["counter_id", "day", "v", "up_v", "down_v", "up_l", "down_l"]])
d = pd.concat(parts)
print("rows", len(d), d.notna().mean().round(2).to_dict())
d = d[d.v.notna()]
g = d.groupby(["counter_id", "day"]).agg(n=("v", "size"), mx=("v", "max"), s=("v", "sum")).reset_index()
g = g[g.n == 12]
peak = (100 * g.mx / g.s).groupby(g.counter_id).median()
t = d.dropna().groupby("counter_id")[["up_v", "down_v", "up_l", "down_l"]].sum()
ldiff = 100 * t.up_l / t.up_v - 100 * t.down_l / t.down_v
cen = {}
for f in sorted(glob.glob("census/kasyo*.csv")):
    for r in list(csv.reader(io.StringIO(open(f, "rb").read().decode("cp932"))))[1:]:
        if r[3] == "3":
            try:
                cen[r[0].zfill(11)] = (float(r[63]), 100 * float(r[39]) / float(r[40]) - 100 * float(r[51]) / float(r[52]))
            except (ValueError, ZeroDivisionError):
                pass
hdr = list(csv.reader(io.StringIO(open("census/kasyo01.csv", "rb").read().decode("cp932"))))[0]
print([hdr[i] for i in (39, 40, 51, 52, 63)])
O["sec11"] = O.census_section
O["pk_c"] = O.counter_id.map(peak); O["pk_s"] = O.sec11.map(lambda s: cen.get(s, (np.nan,))[0])
O["ld_c"] = O.counter_id.map(ldiff); O["ld_s"] = O.sec11.map(lambda s: cen.get(s, (np.nan, np.nan))[1])
O["bad_pk"] = (O.pk_c - O.pk_s).abs() > 1
rng = np.random.default_rng(1)
sh = O.pk_s.values[rng.permutation(len(O))]
print("n", O.pk_c.notna().sum(), "peak |diff|>1: matched", O.bad_pk.mean().round(3), "shuffled", (np.abs(O.pk_c - sh) > 1).mean().round(3))
print(O.groupby("town_in_address").bad_pk.agg(["mean", "size"]).round(3))
print(O.groupby(pd.cut(O.lr, [0, .1, .15, .2])).bad_pk.agg(["sum", "size"]))
q = O[(O.ld_c.abs() > 2) & O.ld_s.notna()]
print("direction: |up-down large diff|>2 pt n", len(q), "sign agree", (np.sign(q.ld_c) == np.sign(q.ld_s)).mean().round(3))
