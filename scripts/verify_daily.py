"""Independent recomputation of daily_summary.csv in a build directory from its hourly files (plain loops over the
rows of 40 sampled counters, not daily.py's code), run by .github/workflows/build-check.yml; stops on any mismatch.
Also checks that every (counter, sensor, day) of the hourly files has exactly one row.
  python scripts/verify_daily.py <build dir>"""
import glob, os, sys, collections
import numpy as np, pandas as pd
import pyarrow.dataset as ds

b = sys.argv[1]
files = sorted(glob.glob(os.path.join(b, "hourly_*.parquet")))
dd = pd.read_csv(os.path.join(b, "daily_summary.csv"), dtype={"customary_break": "string"})
print("daily_summary.csv", len(dd), "rows;", int(dd.vehicles.notna().sum()), "full days")
keys = ds.dataset(files).to_table(columns=["time_jst", "counter_id", "sensor"]).to_pandas()
k = keys.assign(date=keys.time_jst.dt.strftime("%Y-%m-%d"))[["date", "counter_id", "sensor"]].drop_duplicates()
assert len(k) == len(dd), (len(k), len(dd))
assert k.merge(dd[["date", "counter_id", "sensor"]], how="outer", indicator=True)._merge.eq("both").all()

pick = dd[["counter_id", "sensor"]].drop_duplicates().sample(40, random_state=11)
cols = ["time_jst", "counter_id", "sensor", "vehicles", "up_large", "down_large", "baseline_vehicles", "precip_mm",
        "temp_c", "day_off", "days_off_in_run"]
h = ds.dataset(files).to_table(columns=cols, filter=ds.field("counter_id").isin(pick.counter_id.tolist())).to_pandas()
h = h.merge(pick, on=["counter_id", "sensor"])
by = collections.defaultdict(list)
for r in h.itertuples(index=False):
    by[(r.time_jst.strftime("%Y-%m-%d"), r.counter_id, r.sensor)].append(r)
na = lambda x: x is None or (isinstance(x, float) and np.isnan(x)) or x is pd.NA
got = dd.merge(pick, on=["counter_id", "sensor"]).set_index(["date", "counter_id", "sensor"])
n = 0
for key, s in by.items():
    r = got.loc[key]
    v = [x.vehicles for x in s]
    have = [int(x) for x in v if not na(x)]
    assert r.hours == len(have), (key, r.hours, len(have))
    full = len(s) == 24 and len(have) == 24
    exp = sum(have) if full else None
    assert (exp is None and pd.isna(r.vehicles)) or r.vehicles == exp, (key, r.vehicles, exp)
    lg = [None if na(x.vehicles) or na(x.up_large) or na(x.down_large) else int(x.up_large) + int(x.down_large) for x in s]
    exp = sum(lg) if len(s) == 24 and None not in lg else None
    assert (exp is None and pd.isna(r.large_vehicles)) or r.large_vehicles == exp, (key, r.large_vehicles, exp)
    if full:
        top = max(have); exp = min(x.time_jst.hour for x in s if not na(x.vehicles) and int(x.vehicles) == top)
        assert r.peak_hour == exp, (key, r.peak_hour, exp)
    bs = [None if na(x.vehicles) or na(x.baseline_vehicles) else float(x.baseline_vehicles) for x in s]
    exp = round(sum(bs)) if len(s) == 24 and None not in bs else None
    assert (exp is None and pd.isna(r.baseline_vehicles)) or r.baseline_vehicles == exp, (key, r.baseline_vehicles, exp)
    if not pd.isna(r.vehicles) and not pd.isna(r.baseline_vehicles):
        assert abs(r.vs_baseline - r.vehicles / r.baseline_vehicles) <= 0.0005 + 1e-9, key
    ps = [None if na(x.precip_mm) else float(x.precip_mm) for x in s]
    if len(s) == 24 and None not in ps:
        assert abs(r.precip_mm - sum(ps)) <= 0.05 + 1e-9 and r.wet_hours == sum(1 for p in ps if p >= 1), key
    else:
        assert pd.isna(r.precip_mm) and pd.isna(r.wet_hours), key
    ts = [None if na(x.temp_c) else float(x.temp_c) for x in s]
    if len(s) == 24 and None not in ts:
        assert abs(r.temp_c - sum(ts) / 24) <= 0.05 + 1e-9, key
    else:
        assert pd.isna(r.temp_c), key
    assert bool(r.day_off) == bool(s[0].day_off) and r.days_off_in_run == s[0].days_off_in_run, key
    n += 1
print(f"verify_daily: {n} counter-days of {len(pick)} sampled counters recomputed, all equal")
