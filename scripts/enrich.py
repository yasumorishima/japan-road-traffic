"""Columns and files that build_kaggle.py adds on top of the source's columns: the calendar (days off, runs of days
off, customary breaks), a baseline of each counter built only from earlier days, and the nearest AMeDAS weather.

Calendar (per JST date; calendar.csv and three columns on every hourly traffic row)
  day_off          Saturday, Sunday or a national holiday (data/holidays_jp.csv, Cabinet Office)
  days_off_in_run  length of the run of consecutive days off that holds the date (2 = an ordinary weekend,
                   3 = a three-day weekend, 0 on a working day)
  customary_break  "obon" on August 13-16, "new_year" on December 29 - January 3 (the government offices' year-end
                   holidays), empty otherwise. Not days off by law; many firms and families take them off
  calendar.csv also has next_day_off and the holiday names in Japanese and English. The Cabinet Office list calls
  both a substitute holiday (振替休日) and a citizens' holiday between two holidays (国民の休日) "休日"; the two are
  told apart by the rule of the Holiday Act: a "休日" between two named holidays is a citizens' holiday.

Baseline (two columns on every hourly traffic row)
  baseline_vehicles  median of `vehicles` of the same counter and sensor, at the same time of day, on the most recent
                     NBASE earlier days of the same day class within LOOKBACK days before the row's day (at least
                     MINBASE of them). Day class = the weekday (0 Monday ... 6 Sunday), with national holidays counted
                     as Sunday. Days in a customary break, days of 24 counted hours with fewer than STOP_BELOW
                     vehicles (a stopped camera or counter) and rows without `vehicles` are not used as baseline days.
                     A camera or counter that stops for only part of a day is not detected and pulls its baseline
                     down.
                     Only days before the row's day are read, so the column can be used as a feature for forecasting.
  vs_baseline        vehicles / baseline_vehicles (empty when either is empty or the baseline is 0)

Weather (hourly traffic rows: four columns; weather_hourly_*.parquet and weather_stations.csv: the AMeDAS archive)
  Each counter takes, for each element, the nearest AMeDAS station that reports the element (the element appears in
  at least half of the station's hourly rows), if it is within the element's distance; counters.csv gets the
  station ID and distance. Only values with the normal quality flag (aqc 0) are kept. AMeDAS hour_start_jst (the
  observation time minus one hour) is the start of the hour, like time_jst of the traffic: precipitation is the
  rain in that hour, temperature and wind are the readings at its end. Weather exists from 2026-09-29 (the JMA site
  keeps about nine days; the archive starts there); earlier rows are empty."""
import glob, os, re, sys
import numpy as np
import pandas as pd

LOOKBACK = 35
NBASE = 4
MINBASE = 3
# a day of 24 counted hours with fewer vehicles than this is a stopped camera or counter: fewer vehicles than hours.
# Measured 2026-10-17 on 124,164 full days: the 15 with 1 to 23 vehicles are 0.0001 to 4% of their counter's usual
# day (or on two cameras that always read 1); the 50 with 24 to 99 mix stops with a counter whose usual day is 67
STOP_BELOW = 24
MIN_FILLED = 0.9  # share of weather values the join must find (see add_weather)
# share of counters that must get a station (measured 2026-10-16: temperature and wind 2063 of 2069, rain 1754)
MIN_STATION = {"precip_mm": 0.75, "temp_c": 0.9, "wind_ms": 0.9}
CAL_MARGIN = 14  # days of the holiday list needed after the last day of data (runs of days off near the end)
# traffic column, AMeDAS column, distance limit in km
WEATHER = [("precip_mm", "precip_1h_mm", 10), ("temp_c", "temp_c", 20), ("wind_ms", "wind_ms", 20),
           ("snow_depth_cm", "snow_depth_cm", 20)]
CAL_ROW = ["day_off", "days_off_in_run", "customary_break"]
BASE_ROW = ["baseline_vehicles", "vs_baseline"]
WEATHER_ROW = [c for c, _, _ in WEATHER]
STATION_COLS = [f"{c.split('_')[0]}_station_{s}" for c, _, _ in WEATHER for s in ("id", "km")]
HOLIDAY_EN = {"元日": "New Year's Day", "成人の日": "Coming of Age Day", "建国記念の日": "National Foundation Day",
              "天皇誕生日": "The Emperor's Birthday", "春分の日": "Vernal Equinox Day", "昭和の日": "Showa Day",
              "憲法記念日": "Constitution Memorial Day", "みどりの日": "Greenery Day", "こどもの日": "Children's Day",
              "海の日": "Marine Day", "山の日": "Mountain Day", "敬老の日": "Respect for the Aged Day",
              "秋分の日": "Autumnal Equinox Day", "スポーツの日": "Sports Day", "文化の日": "Culture Day",
              "勤労感謝の日": "Labor Thanksgiving Day"}
CALENDAR_COLS = ["date", "weekday", "is_holiday", "holiday_name", "holiday_name_en", "day_off", "days_off_in_run",
                 "next_day_off", "customary_break"]


def calendar(path):
    """One row per day of the years in the holiday list."""
    h = pd.read_csv(path, dtype=str)
    names = dict(zip(pd.to_datetime(h.date).dt.date, h.name))
    years = sorted({d.year for d in names})
    days = pd.date_range(f"{years[0]}-01-01", f"{years[-1]}-12-31", freq="D")
    c = pd.DataFrame({"date": days.date, "weekday": days.dayofweek.astype("int8")})
    c["is_holiday"] = c.date.isin(set(names))
    ja = [names.get(d, "") for d in c.date]
    named = [n not in ("", "休日") for n in ja]
    out_ja, out_en = [], []
    for i, n in enumerate(ja):
        if n == "休日":
            between = 0 < i < len(ja) - 1 and named[i - 1] and named[i + 1]
            out_ja.append("国民の休日" if between else "振替休日")
            out_en.append("Citizens' Holiday" if between else "Substitute Holiday")
        elif n:
            if n not in HOLIDAY_EN:
                sys.exit(f"{path}: no English name for holiday {n!r}: add it to enrich.HOLIDAY_EN")
            out_ja.append(n); out_en.append(HOLIDAY_EN[n])
        else:
            out_ja.append(""); out_en.append("")
    c["holiday_name"], c["holiday_name_en"] = out_ja, out_en
    off = ((c.weekday >= 5) | c.is_holiday).to_numpy()
    run = np.zeros(len(c), np.int8)
    i = 0
    while i < len(c):
        if off[i]:
            j = i
            while j < len(c) and off[j]:
                j += 1
            run[i:j] = j - i
            i = j
        else:
            i += 1
    c["day_off"], c["days_off_in_run"] = off, run
    c["next_day_off"] = np.r_[off[1:], False]
    md = [(d.month, d.day) for d in c.date]
    c["customary_break"] = ["obon" if (8, 13) <= x <= (8, 16) else
                            "new_year" if x >= (12, 29) or x <= (1, 3) else "" for x in md]
    return c[CALENDAR_COLS]


def check_span(cal, last_day):
    end = cal.date.max()
    if pd.Timestamp(last_day) + pd.Timedelta(days=CAL_MARGIN) > pd.Timestamp(end):
        sys.exit(f"data reaches {last_day} but the holiday list ends on {end}: add the next year to "
                 f"data/holidays_jp.csv from the Cabinet Office list")


def add_calendar(df, cal):
    c = cal.set_index("date")
    d = df.time_jst.dt.date
    df["day_off"] = d.map(c.day_off).astype("bool")
    df["days_off_in_run"] = d.map(c.days_off_in_run).astype("int8")
    df["customary_break"] = pd.array(d.map(c.customary_break).replace("", None), "string")
    return df


KEYS = ["counter_id", "sensor", "tod", "cls"]


def baseline(df, cal, prev):
    """Add BASE_ROW to df (one ten-day chunk of one resolution, after add_calendar and the vehicles column) and
    return the baseline days to carry to the next chunk. prev: baseline days of the earlier chunks (or None)."""
    c = cal.set_index("date")
    date = df.time_jst.dt.date
    hol = date.map(c.is_holiday).to_numpy(bool)
    t = pd.DataFrame({"counter_id": df.counter_id.to_numpy(), "sensor": df.sensor.to_numpy(),
                      "tod": (df.time_jst.dt.hour * 60 + df.time_jst.dt.minute).astype("int16").to_numpy(),
                      "cls": np.where(hol, 6, df.time_jst.dt.dayofweek).astype("int8"),
                      "day": (df.time_jst.dt.normalize() - pd.Timestamp("2000-01-01")).dt.days.astype("int32").to_numpy(),
                      "v": df.vehicles.astype("float64").to_numpy(), "row": np.arange(len(df))})
    # a day of 24 counted hours with fewer vehicles than hours is a stopped camera or counter (stopped in
    # daily_summary.csv): not a baseline day. 24 values mean 24 rows: build_kaggle stops on a repeated (time, counter)
    gz = t.groupby(["counter_id", "sensor", "day"], sort=False).v
    stopped = (gz.transform("count").eq(24) & gz.transform("sum").lt(STOP_BELOW)).to_numpy()
    src = t.v.notna().to_numpy() & df.customary_break.isna().to_numpy() & ~stopped
    days = pd.concat([prev, t.loc[src, KEYS + ["day", "v"]]], ignore_index=True) if prev is not None \
        else t.loc[src, KEYS + ["day", "v"]].reset_index(drop=True)
    days = days.sort_values(KEYS + ["day"], kind="stable").reset_index(drop=True)
    g = days.groupby(KEYS, sort=False)
    lag = {"v0": days.v, "d0": days.day}
    for k in range(1, NBASE):
        lag[f"v{k}"] = g.v.shift(k)
        lag[f"d{k}"] = g.day.shift(k)
    lagged = pd.concat([days[KEYS], pd.DataFrame(lag)], axis=1)
    # the latest baseline day strictly before the row's day carries the NBASE most recent ones
    m = pd.merge_asof(t.sort_values("day", kind="stable"), lagged.assign(day=lagged.d0).sort_values("day", kind="stable"),
                      on="day", by=KEYS, allow_exact_matches=False, direction="backward").sort_values("row")
    assert len(m) == len(df) and (m.row.to_numpy() == np.arange(len(df))).all()
    V = m[[f"v{k}" for k in range(NBASE)]].to_numpy("float64")
    D = m[[f"d{k}" for k in range(NBASE)]].to_numpy("float64")
    day = m.day.to_numpy("float64")[:, None]
    ok = np.isfinite(V) & np.isfinite(D) & (D < day) & (day - D <= LOOKBACK)
    V = np.where(ok, V, np.nan)
    n = ok.sum(axis=1)
    med = np.full(len(df), np.nan)
    has = n >= MINBASE
    med[has] = np.nanmedian(V[has], axis=1)
    veh = df.vehicles.astype("float64").to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        vs = np.where(np.isfinite(med) & (med > 0), veh / med, np.nan)
    df["baseline_vehicles"] = pd.array(np.round(med, 1), "Float32")   # NaN becomes <NA>
    df["vs_baseline"] = pd.array(np.round(vs, 3), "Float32")
    keep = days[days.day > days.day.max() - LOOKBACK - 1]
    return keep.reset_index(drop=True)


AMEDAS = re.compile(r"^amedas_(\d{8})\.csv\.gz$")
STATIONS = re.compile(r"^amedas_stations_(\d{6})\.csv\.gz$")


def load_weather(wdir):
    """(hourly weather of every station with values of other quality flags blanked, station table, elements of
    each station). Stops when there are no files or a (time, station) repeats."""
    files = sorted(p for p in glob.glob(os.path.join(wdir, "**", "*.csv.gz"), recursive=True)
                   if AMEDAS.match(os.path.basename(p)))
    tables = sorted(p for p in glob.glob(os.path.join(wdir, "**", "*.csv.gz"), recursive=True)
                    if STATIONS.match(os.path.basename(p)))
    if not files or not tables:
        sys.exit(f"{wdir}: no AMeDAS day files or no station table")
    w = pd.concat([pd.read_csv(p) for p in files], ignore_index=True)
    elems = [c for c in w.columns if c + "_aqc" in w.columns]
    has = w.groupby("station_id")[[e + "_aqc" for e in elems]].agg(lambda s: s.notna().mean() >= 0.5)
    has.columns = elems
    for e in elems:
        w[e] = w[e].where(w[e + "_aqc"] == 0)
    w["time_jst"] = pd.to_datetime(w.hour_start_jst.astype("int64").astype(str), format="%Y%m%d%H%M")
    w = w[["time_jst", "station_id"] + elems].sort_values(["time_jst", "station_id"]).reset_index(drop=True)
    if w.duplicated(["time_jst", "station_id"]).any():
        sys.exit(f"{wdir}: a station repeats within an hour (two copies of a day file?)")
    st = pd.read_csv(tables[-1], dtype={"elems": str})
    return w, st, has


def km(lat1, lon1, lat2, lon2):
    la1, lo1 = np.radians(np.asarray(lat1, float))[:, None], np.radians(np.asarray(lon1, float))[:, None]
    la2, lo2 = np.radians(np.asarray(lat2, float))[None, :], np.radians(np.asarray(lon2, float))[None, :]
    a = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


def nearest_stations(counters, st, has):
    """STATION_COLS for each counter (rows of counters.csv, which has latitude and longitude)."""
    st = st[st.station_id.isin(has.index)].reset_index(drop=True)
    lat = pd.to_numeric(counters.latitude, errors="coerce").to_numpy()
    lon = pd.to_numeric(counters.longitude, errors="coerce").to_numpy()
    D = km(lat, lon, st.lat, st.lon)
    D = np.where(np.isfinite(D), D, np.inf)  # a station or counter without coordinates is never the nearest
    out = {}
    for col, el, lim in WEATHER:
        name = col.split("_")[0]
        ok = has.reindex(st.station_id)[el].fillna(False).to_numpy(bool)
        Dm = np.where(ok[None, :], D, np.inf)
        j = np.argmin(Dm, axis=1)
        d = Dm[np.arange(len(Dm)), j]
        near = np.isfinite(d) & (d <= lim)
        out[f"{name}_station_id"] = pd.array(np.where(near, st.station_id.to_numpy()[j], np.nan), "Int32")
        out[f"{name}_station_km"] = pd.array(np.where(near, np.round(d, 2), np.nan), "Float32")
        if col in MIN_STATION and len(counters) >= 100 and near.mean() < MIN_STATION[col]:
            sys.exit(f"{col}: only {near.mean():.1%} of the counters got a station within {lim} km")
    return pd.DataFrame(out, index=counters.index)


def add_weather(df, w, near):
    """WEATHER_ROW on df from the station of each (counter, sensor) in near (indexed by counter_id, sensor)."""
    k = pd.DataFrame({"counter_id": df.counter_id.to_numpy(), "sensor": df.sensor.to_numpy(),
                      "time_jst": df.time_jst.dt.floor("h").to_numpy()})
    k = k.merge(near, how="left", left_on=["counter_id", "sensor"], right_index=True, validate="many_to_one")
    for col, el, _ in WEATHER:
        sid = f"{col.split('_')[0]}_station_id"
        x = k[["time_jst", sid]].astype({sid: "float64"}).merge(
            w[["time_jst", "station_id", el]].astype({"station_id": "float64"}), how="left",
            left_on=["time_jst", sid], right_on=["time_jst", "station_id"], validate="many_to_one")
        assert len(x) == len(df)
        v = x[el].to_numpy("float64")
        # a join that silently finds nothing (time or ID format) must stop the build: where the archive has the hour
        # and the counter has a station, precipitation, temperature and wind are almost always there (>= 99%)
        inside = k.time_jst.isin(w.time_jst.unique()).to_numpy() & k[sid].notna().to_numpy()
        if el != "snow_depth_cm" and inside.sum() >= 1000 and np.isfinite(v[inside]).mean() < MIN_FILLED:
            sys.exit(f"{col}: only {np.isfinite(v[inside]).mean():.1%} of the rows with a station and an archived "
                     f"hour got a value")
        df[col] = pd.array(v, "Float32")
    return df
