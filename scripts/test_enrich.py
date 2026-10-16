"""Tests of enrich.py: the calendar rules on the real holiday list, the baseline reads only earlier days (a value
planted on the day itself or later never moves it), carrying the baseline days between chunks gives the same
result as one chunk, and the weather join lines up the hour and stops when it finds nothing."""
import os, subprocess, sys, tempfile
import numpy as np
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import enrich

HOL = os.path.join(HERE, "..", "data", "holidays_jp.csv")
cal = enrich.calendar(HOL)
c = cal.set_index("date")
D = lambda s: pd.Timestamp(s).date()
# 1. calendar
assert c.loc[D("2026-09-22"), "holiday_name_en"] == "Citizens' Holiday" and c.loc[D("2026-09-22"), "holiday_name"] == "国民の休日"
assert c.loc[D("2026-05-06"), "holiday_name_en"] == "Substitute Holiday"  # Constitution Day fell on a Sunday
assert c.loc[D("2026-09-21"), "holiday_name_en"] == "Respect for the Aged Day"
assert [c.loc[D(f"2026-09-{d}"), "days_off_in_run"] for d in range(18, 25)] == [0, 5, 5, 5, 5, 5, 0]
assert c.loc[D("2026-10-10"), "days_off_in_run"] == 3 and c.loc[D("2026-10-17"), "days_off_in_run"] == 2
assert bool(c.loc[D("2026-10-16"), "next_day_off"]) and not c.loc[D("2026-10-14"), "next_day_off"]
assert c.loc[D("2026-08-13"), "customary_break"] == "obon" and c.loc[D("2026-08-17"), "customary_break"] == ""
assert c.loc[D("2026-08-16"), "customary_break"] == "obon" and c.loc[D("2026-08-12"), "customary_break"] == ""
assert c.loc[D("2026-12-29"), "customary_break"] == "new_year" and c.loc[D("2027-01-03"), "customary_break"] == "new_year"
assert c.loc[D("2027-01-04"), "customary_break"] == "" and not c.loc[D("2026-08-14"), "day_off"]  # obon Friday: not by law
assert set(cal.holiday_name_en[cal.is_holiday]) - {""} and (cal.holiday_name_en[cal.is_holiday] != "").all()
assert (cal.holiday_name[~cal.is_holiday] == "").all()
try:
    enrich.check_span(cal, D("2027-12-20"))
    raise AssertionError("check_span let data run past the list")
except SystemExit as e:
    assert "holiday list ends" in str(e)
enrich.check_span(cal, D("2027-12-17"))


# 2. baseline: synthetic hourly rows, two counters, 70 days
def frame(days, value):
    t = pd.date_range("2026-07-01", periods=days * 24, freq="h")
    df = pd.DataFrame({"time_jst": np.tile(t, 2), "counter_id": np.repeat([1, 2], len(t)).astype("int32"),
                       "sensor": np.repeat(["loop", "cctv"], len(t))})
    df["vehicles"] = pd.array(value(df), "Int32")
    return enrich.add_calendar(df, cal)


rng = np.random.default_rng(0)
base = frame(70, lambda df: rng.integers(50, 500, len(df)))
full = base.copy()
enrich.baseline(full, cal, None)
# the first three weeks have fewer than 3 earlier days of their class: about 70% of 70 days get a baseline
assert 0.6 < full.baseline_vehicles.notna().mean() < 0.75, full.baseline_vehicles.notna().mean()
# naive recomputation of every row of one counter at 08:00
for r in full[(full.counter_id == 1) & (full.time_jst.dt.hour == 8)].itertuples():
    d = r.time_jst.normalize()
    cls = 6 if c.loc[d.date(), "is_holiday"] else d.dayofweek
    e = full[(full.counter_id == 1) & (full.time_jst.dt.hour == 8) & (full.time_jst.dt.normalize() < d)
             & full.customary_break.isna()]
    e = e[[6 if c.loc[x.date(), "is_holiday"] else x.dayofweek for x in e.time_jst.dt.normalize()] == np.int64(cls)]
    e = e.tail(4)
    e = e[(d - e.time_jst.dt.normalize()).dt.days <= 35]
    exp = round(float(np.median(e.vehicles.astype(float))), 1) if len(e) >= 3 else None
    got = None if pd.isna(r.baseline_vehicles) else float(r.baseline_vehicles)
    assert (exp is None and got is None) or abs(exp - got) < 0.051, (r.time_jst, exp, got)
# planting a huge value on day X never changes the baseline of day X or earlier
for X in ("2026-08-05", "2026-08-20"):
    planted = base.copy()
    on = planted.time_jst.dt.normalize() >= pd.Timestamp(X)
    planted.loc[on, "vehicles"] = 99999
    enrich.baseline(planted, cal, None)
    upto = planted.time_jst.dt.normalize() <= pd.Timestamp(X)
    a, b = full.baseline_vehicles[upto].astype(float), planted.baseline_vehicles[upto].astype(float)
    assert ((a == b) | (a.isna() & b.isna())).all(), X
    later = planted.time_jst.dt.normalize() > pd.Timestamp(X) + pd.Timedelta(days=7)
    assert (planted.baseline_vehicles[later].dropna() == 99999).any()  # and it does reach later days
# a customary break day is not used as a baseline day
ob = base.copy()
ob.loc[ob.customary_break.notna(), "vehicles"] = 99999
enrich.baseline(ob, cal, None)
assert not (ob.baseline_vehicles.dropna() == 99999).any()
# carrying the baseline days across chunks gives the result of one chunk
parts, prev = [], None
for lo, hi in (("2026-07-01", "2026-07-21"), ("2026-07-21", "2026-08-11"), ("2026-08-11", "2026-09-30")):
    p = base[(base.time_jst >= lo) & (base.time_jst < hi)].copy().reset_index(drop=True)
    prev = enrich.baseline(p, cal, prev)
    parts.append(p)
chunked = pd.concat(parts, ignore_index=True)
a, b = full.baseline_vehicles.astype(float).to_numpy(), chunked.baseline_vehicles.astype(float).to_numpy()
assert ((a == b) | (np.isnan(a) & np.isnan(b))).all()
v = full.vehicles.astype(float) / full.baseline_vehicles.astype(float)
ok = full.vs_baseline.notna()
assert ok.any() and np.allclose(full.vs_baseline[ok].astype(float), v[ok].round(3), atol=6e-4)

# 3. weather join
w = pd.DataFrame({"time_jst": pd.date_range("2026-10-01", periods=48, freq="h").repeat(2),
                  "station_id": np.tile([100, 200], 48)})
w["precip_1h_mm"] = np.arange(96, dtype=float)
w["temp_c"] = w.precip_1h_mm + 1000
w["wind_ms"] = w.precip_1h_mm + 2000
w["snow_depth_cm"] = np.nan
near = pd.DataFrame({"precip_station_id": pd.array([200, None], "Int32"), "temp_station_id": pd.array([100, 100], "Int32"),
                     "wind_station_id": pd.array([100, 200], "Int32"), "snow_station_id": pd.array([None, None], "Int32")},
                    index=pd.MultiIndex.from_tuples([(1, "loop"), (2, "cctv")], names=["counter_id", "sensor"]))
df = pd.DataFrame({"time_jst": pd.to_datetime(["2026-10-01 05:00", "2026-10-01 05:00", "2026-10-02 23:00", "2026-09-01 00:00"]),
                   "counter_id": np.array([1, 2, 1, 1], "int32"), "sensor": ["loop", "cctv", "loop", "loop"]})
enrich.add_weather(df, w, near)
assert df.precip_mm.iloc[0] == 11 and pd.isna(df.precip_mm.iloc[1]) and df.precip_mm.iloc[2] == 95
assert df.temp_c.iloc[0] == 1010 and df.temp_c.iloc[1] == 1010 and df.wind_ms.iloc[1] == 2011
assert df[["precip_mm", "temp_c", "wind_ms"]].iloc[3].isna().all() and df.snow_depth_cm.isna().all()
# a join that finds nothing stops the build
big = pd.DataFrame({"time_jst": pd.date_range("2026-10-01", periods=48, freq="h").repeat(30),
                    "counter_id": np.tile(np.ones(30, "int32"), 48), "sensor": "loop"})
w2 = w.copy(); w2["time_jst"] = w2.time_jst + pd.Timedelta(minutes=30)  # misaligned hours
w2 = pd.concat([w2, w.assign(station_id=999)])                          # the hours exist, the station's values do not
try:
    enrich.add_weather(big, w2, near)
    raise AssertionError("an empty join passed")
except SystemExit as e:
    assert "got a value" in str(e)
enrich.add_weather(big.copy(), w, near)
print("test_enrich: ALL PASS")
