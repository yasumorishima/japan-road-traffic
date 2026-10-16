"""Tests for daily.py on made-up hourly rows, each day built to exercise one rule, and a naive recomputation (plain
loops over the rows, not daily.py's code) that must agree with it on every value."""
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily

rng = np.random.default_rng(7)
rows = []


def day(date, cid, sensor="loop", hours=range(24), veh=None, large=None, base=None, rain=None, temp=None):
    for h in hours:
        rows.append(dict(time_jst=pd.Timestamp(f"{date} {h:02d}:00"), counter_id=cid, sensor=sensor, road_type=3,
                         prefecture_en="Kanagawa", weekday=pd.Timestamp(date).dayofweek, is_holiday=False,
                         day_off=pd.Timestamp(date).dayofweek >= 5, days_off_in_run=0, customary_break=None,
                         vehicles=veh(h) if veh else int(rng.integers(100, 900)),
                         up_large=large(h) if large else int(rng.integers(0, 50)), down_large=int(rng.integers(0, 50)),
                         baseline_vehicles=base(h) if base else float(rng.integers(100, 900)),
                         precip_mm=rain(h) if rain else float(rng.choice([0, 0, 0, 0.5, 1.0, 3.5])),
                         temp_c=temp(h) if temp else float(rng.normal(18, 3))))


day("2026-10-01", 1)                                                    # full day
day("2026-10-01", 2, hours=[h for h in range(24) if h != 5])            # 23 rows: totals blank
day("2026-10-01", 3, veh=lambda h: None if h == 7 else 500)             # a flagged hour: vehicles blank
day("2026-10-01", 4, veh=lambda h: 900 if h in (8, 17) else 100)        # tie for the peak: the earlier hour
day("2026-10-01", 5, large=lambda h: None if h == 3 else 10)            # large blank in one hour
day("2026-10-01", 6, base=lambda h: None if h == 0 else 300.0)          # no baseline in one hour
day("2026-10-01", 7, rain=lambda h: None if h == 23 else 2.0)           # rain missing in one hour
day("2026-10-01", 8, rain=lambda h: [0.0, 0.5, 1.0, 1.5][h % 4])        # wet hours count >= 1 mm
day("2026-10-01", 9, veh=lambda h: None)                                # every hour flagged: hours 0
day("2026-10-01", 10, sensor="cctv")                                    # same ID, other sensor
day("2026-10-01", 10, sensor="loop")
day("2026-10-02", 1)                                                    # the next day of counter 1
df = pd.DataFrame(rows)
for c in ("vehicles", "up_large", "down_large"):
    df[c] = df[c].astype("Int32")
df["customary_break"] = df.customary_break.astype("string")
out = daily.summarise(df)
assert list(out.columns) == daily.COLS
assert len(out) == 12 and not out.duplicated(["date", "counter_id", "sensor"]).any()

# naive recomputation
for r in out.itertuples(index=False):
    s = [x for x in rows if x["time_jst"].strftime("%Y-%m-%d") == r.date and x["counter_id"] == r.counter_id
         and x["sensor"] == r.sensor]
    v = [x["vehicles"] for x in s]
    have = [x for x in v if x is not None]
    assert r.hours == len(have), (r, len(have))
    full_v = len(s) == 24 and len(have) == 24
    exp = sum(have) if full_v else None
    assert (pd.isna(r.vehicles) and exp is None) or r.vehicles == exp, (r.counter_id, r.vehicles, exp)
    lg = [x["up_large"] + x["down_large"] if x["up_large"] is not None and x["vehicles"] is not None else None for x in s]
    exp = sum(lg) if len(s) == 24 and None not in lg else None
    assert (pd.isna(r.large_vehicles) and exp is None) or r.large_vehicles == exp, (r.counter_id, r.large_vehicles, exp)
    if full_v:
        best = max(v); exp = min(x["time_jst"].hour for x in s if x["vehicles"] == best)
        assert r.peak_hour == exp, (r.counter_id, r.peak_hour, exp)
    else:
        assert pd.isna(r.peak_hour)
    b = [x["baseline_vehicles"] if x["vehicles"] is not None else None for x in s]
    exp = round(sum(b)) if len(s) == 24 and None not in b else None
    assert (pd.isna(r.baseline_vehicles) and exp is None) or r.baseline_vehicles == exp, (r.counter_id, r.baseline_vehicles, exp)
    if not pd.isna(r.vehicles) and not pd.isna(r.baseline_vehicles):
        assert abs(r.vs_baseline - round(r.vehicles / r.baseline_vehicles, 3)) < 1e-9
    else:
        assert pd.isna(r.vs_baseline)
    p = [x["precip_mm"] for x in s]
    if len(s) == 24 and None not in p:
        assert abs(r.precip_mm - round(sum(p), 1)) < 1e-9 and r.wet_hours == sum(1 for x in p if x >= 1)
    else:
        assert pd.isna(r.precip_mm) and pd.isna(r.wet_hours)
    t = [x["temp_c"] for x in s]
    assert abs(r.temp_c - round(sum(t) / 24, 1)) < 1e-9 if len(s) == 24 else pd.isna(r.temp_c)
    assert r.weekday == s[0]["weekday"] and r.day_off == s[0]["day_off"] and r.prefecture_en == "Kanagawa"

# the cases, by hand
o = out.set_index(["date", "counter_id", "sensor"])
assert pd.isna(o.loc[("2026-10-01", 2, "loop"), "vehicles"]) and o.loc[("2026-10-01", 2, "loop"), "hours"] == 23
assert pd.isna(o.loc[("2026-10-01", 3, "loop"), "vehicles"]) and o.loc[("2026-10-01", 3, "loop"), "hours"] == 23
assert o.loc[("2026-10-01", 4, "loop"), "peak_hour"] == 8
assert pd.isna(o.loc[("2026-10-01", 5, "loop"), "large_vehicles"]) and not pd.isna(o.loc[("2026-10-01", 5, "loop"), "vehicles"])
assert pd.isna(o.loc[("2026-10-01", 6, "loop"), "vs_baseline"]) and not pd.isna(o.loc[("2026-10-01", 6, "loop"), "vehicles"])
assert pd.isna(o.loc[("2026-10-01", 7, "loop"), "precip_mm"])
assert o.loc[("2026-10-01", 8, "loop"), "wet_hours"] == 12 and o.loc[("2026-10-01", 8, "loop"), "precip_mm"] == 18.0
assert o.loc[("2026-10-01", 9, "loop"), "hours"] == 0 and pd.isna(o.loc[("2026-10-01", 9, "loop"), "vehicles"])
assert ("2026-10-01", 10, "cctv") in o.index and ("2026-10-01", 10, "loop") in o.index
# more than 24 rows for one counter-day stops it
try:
    daily.summarise(pd.concat([df, df.iloc[[0]]], ignore_index=True))
    raise SystemExit("a duplicated hour was accepted")
except ValueError:
    pass
print("test_daily: ALL PASS")
