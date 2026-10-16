"""Independent recomputation of the columns added by enrich.py in a build directory (naive loops, not enrich.py's
code), run by .github/workflows/build-check.yml. Reads only the needed columns of 40 sampled counters; stops on any
mismatch.  python scripts/verify_enrich.py <build dir> <weather dir>"""
import glob, os, sys
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pyarrow.dataset as ds

b, wd = sys.argv[1], sys.argv[2]
for p in sorted(glob.glob(os.path.join(b, "*.parquet"))):
    print(os.path.basename(p), pq.read_metadata(p).num_rows, "rows", len(pq.read_schema(p).names), "cols")
cal = pd.read_csv(os.path.join(b, "calendar.csv"), keep_default_na=False)
print(cal[cal.date.between("2026-09-18", "2026-09-24")].to_string())
print(cal[cal.holiday_name != ""].to_string())
print(cal.customary_break.value_counts().to_dict())

cn = pd.read_csv(os.path.join(b, "counters.csv"))
pick = cn.sample(40, random_state=3)[["counter_id", "sensor"]]
cols = ["time_jst", "counter_id", "sensor", "vehicles", "day_off", "days_off_in_run", "customary_break",
        "baseline_vehicles", "vs_baseline", "precip_mm", "temp_c", "wind_ms", "snow_depth_cm"]
files = sorted(glob.glob(os.path.join(b, "hourly_*.parquet")))
h = ds.dataset(files).to_table(columns=cols, filter=ds.field("counter_id").isin(pick.counter_id.tolist())).to_pandas()
h = h.merge(pick, on=["counter_id", "sensor"])
print("sampled hourly rows", len(h))
c = cal.set_index("date")
d = h.time_jst.dt.strftime("%Y-%m-%d")
assert (h.day_off.to_numpy() == d.map(c.day_off).astype(bool).to_numpy()).all()
assert (h.days_off_in_run.to_numpy() == d.map(c.days_off_in_run).to_numpy()).all()
assert (h.customary_break.fillna("").to_numpy() == d.map(c.customary_break).to_numpy()).all()
print("calendar columns agree")

hol = set(cal.date[cal.is_holiday])
h["date"] = h.time_jst.dt.normalize()
h["cls"] = np.where(d.isin(hol), 6, h.time_jst.dt.dayofweek)
h["hod"] = h.time_jst.dt.hour
src = h[h.vehicles.notna() & h.customary_break.isna()]
bad = n_has = 0
sample = h.sample(3000, random_state=1)
for (cid, sen, hod, cls), g in sample.groupby(["counter_id", "sensor", "hod", "cls"]):
    s = src[(src.counter_id == cid) & (src.sensor == sen) & (src.hod == hod) & (src.cls == cls)].sort_values("date")
    for r in g.itertuples():
        e = s[s.date < r.date].tail(4)
        e = e[(r.date - e.date).dt.days <= 35]
        exp = float(np.round(np.median(e.vehicles.astype(float)), 1)) if len(e) >= 3 else np.nan
        got = np.nan if pd.isna(r.baseline_vehicles) else float(r.baseline_vehicles)
        n_has += np.isfinite(exp)
        if not ((np.isnan(exp) and np.isnan(got)) or abs(exp - got) < 0.051):
            bad += 1
            if bad <= 5:
                print("MISMATCH", r.time_jst, cid, sen, exp, got)
print(f"baseline: {bad} mismatches in {len(sample)} sampled rows ({n_has} with a baseline)")
assert bad == 0
ok = h.vs_baseline.notna()
vs = h.vehicles.astype(float) / h.baseline_vehicles.astype(float)
print("vs_baseline max abs err", float(np.max(np.abs(vs[ok] - h.vs_baseline[ok].astype(float)))),
      "median", float(h.vs_baseline.median()), "share with baseline", round(float(h.baseline_vehicles.notna().mean()), 3))

raw = pd.concat([pd.read_csv(p, usecols=["hour_start_jst", "station_id", "precip_1h_mm", "precip_1h_mm_aqc", "temp_c",
                                         "temp_c_aqc", "wind_ms", "wind_ms_aqc"])
                 for p in sorted(glob.glob(os.path.join(wd, "amedas_2026*.csv.gz")))])
raw["t"] = pd.to_datetime(raw.hour_start_jst.astype(str), format="%Y%m%d%H%M")
rawi = raw.set_index(["t", "station_id"])
ci = cn.set_index(["counter_id", "sensor"])
s2 = h[h.time_jst >= "2026-09-29"]
s2 = s2.sample(min(2000, len(s2)), random_state=2)
bad = 0
for r in s2.itertuples():
    for col, el, name in (("precip_mm", "precip_1h_mm", "precip"), ("temp_c", "temp_c", "temp"), ("wind_ms", "wind_ms", "wind")):
        sid = ci.loc[(r.counter_id, r.sensor), f"{name}_station_id"]
        got = getattr(r, col)
        exp = np.nan
        if pd.notna(sid) and (r.time_jst, int(sid)) in rawi.index:
            x = rawi.loc[(r.time_jst, int(sid))]
            exp = x[el] if x[el + "_aqc"] == 0 else np.nan
        if not ((pd.isna(exp) and pd.isna(got)) or abs(float(exp) - float(got)) < 1e-3):
            bad += 1
            if bad <= 5:
                print("WX MISMATCH", r.time_jst, r.counter_id, col, exp, got)
print("weather mismatches", bad, "of", 3 * len(s2))
assert bad == 0
after = h[h.time_jst >= "2026-09-29"]
print("filled after 09-29:", {k: round(float(after[k].notna().mean()), 3) for k in ("precip_mm", "temp_c", "wind_ms", "snow_depth_cm")})
print("filled before:", {k: int(h[h.time_jst < "2026-09-29"][k].notna().sum()) for k in ("precip_mm", "temp_c")})
print("stations within limit:", {k: int(cn[k].notna().sum()) for k in cn.columns if k.endswith("_station_id")}, "of", len(cn))
print("km max", {k: float(cn[k].max()) for k in cn.columns if k.endswith("_station_km")})
