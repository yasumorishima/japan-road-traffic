"""daily_summary.csv: one row per counter, sensor and day (JST) from the hourly files, the small file to start from.
A day's totals are given only when all 24 hours carry the value (a counter with a missing or flagged hour has
`hours` < 24 and blank totals), so a total never mixes a short day with a full one. A day whose 24 hours carry a count
but add up to fewer vehicles than hours (enrich.STOP_BELOW) is marked stopped and its counts are left blank: on
national highways and expressways that is a stopped camera or counter, not an empty road (through 2026-10-15: 2,237
days of 24 zero hours, 2,137 of them CCTV, and 15 days of 1 to 23 vehicles, 13 of them on counters whose usual day is
hundreds to thousands). The hourly baseline leaves such days out too (enrich.py). vs_baseline follows the hourly
rule: empty unless the day's baseline is above 0."""
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from enrich import STOP_BELOW

CAL = ["weekday", "is_holiday", "day_off", "days_off_in_run", "customary_break"]
COLS = ["date", "counter_id", "sensor", "road_type", "prefecture_en", *CAL, "hours", "stopped", "vehicles", "large_vehicles",
        "peak_hour", "baseline_vehicles", "vs_baseline", "precip_mm", "wet_hours", "temp_c"]


def summarise(df):
    """Daily rows of one hourly chunk (after enrich: vehicles, the calendar, baseline and weather columns)."""
    d = pd.DataFrame({
        "date": df.time_jst.dt.strftime("%Y-%m-%d"), "counter_id": df.counter_id, "sensor": df.sensor,
        "road_type": df.road_type, "prefecture_en": df.prefecture_en,
        **{c: df[c] for c in CAL},
        "hour": df.time_jst.dt.hour,
        "v": df.vehicles.astype("float64"),
        "lg": df.up_large.astype("float64") + df.down_large.astype("float64"),  # used only on counted days
        "b": df.baseline_vehicles.astype("float64").where(df.vehicles.notna()),
        "p": df.precip_mm.astype("float64"), "t": df.temp_c.astype("float64")})
    d["wet"] = (d.p >= 1).astype("float64").where(d.p.notna())
    g = d.groupby(["date", "counter_id", "sensor"], sort=True)
    out = g[["road_type", "prefecture_en", *CAL]].first()
    n = g.size()
    if (n > 24).any():
        raise ValueError(f"more than 24 hourly rows for {n[n > 24].index[0]}")
    full = lambda s: g[s].count().eq(24) & n.eq(24)  # all 24 hours present and carrying the value
    out["hours"] = g.v.count().astype("int8")
    stopped = full("v") & g.v.sum().lt(STOP_BELOW)
    out["stopped"] = stopped.astype(bool)
    counted = full("v") & ~stopped
    out["vehicles"] = g.v.sum().where(counted).round().astype("Int32")
    out["large_vehicles"] = g.lg.sum().where(full("lg") & counted).round().astype("Int32")
    peak = d.loc[d.v.notna()].sort_values(["v", "hour"], ascending=[False, True]).groupby(
        ["date", "counter_id", "sensor"]).hour.first()  # the busiest hour; the earliest one on a tie
    out["peak_hour"] = peak.reindex(out.index).where(counted).astype("Int8")
    out["baseline_vehicles"] = g.b.sum().where(full("b")).round().astype("Int32")
    bl = out.baseline_vehicles.astype("float64")
    out["vs_baseline"] = (out.vehicles.astype("float64") / bl).where(bl > 0).round(3)
    out["precip_mm"] = g.p.sum().where(full("p")).round(1)
    out["wet_hours"] = g.wet.sum().where(full("p")).round().astype("Int8")
    out["temp_c"] = g.t.mean().where(full("t")).round(1)
    return out.reset_index()[COLS]
