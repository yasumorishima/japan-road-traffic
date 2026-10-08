"""Weather collector: hourly JMA AMeDAS observations at every station, one gzip CSV per JST day, uploaded to
the release `amedas-YYYYMM` (plus the station table once per month, `amedas_stations_YYYYMM.csv.gz`).

The JMA site (https://www.jma.go.jp/bosai/amedas/) keeps its 10-minute station maps for only about 9 days
(measured 2026-10-08: 09-28 12:00 still served, 09-27 gone), so this runs three times a day and fetches every
day the release does not have yet. Only observations are stored; no forecast or warning is redistributed.
Terms: JMA website terms of use, compatible with CC BY 4.0 (Public Data License 1.0); credit "Japan
Meteorological Agency".

Day D holds the 24 hourly maps from D 01:00 to D+1 00:00. Each value of the map covers the preceding hour
(precipitation, sunshine) or is the reading at that moment (temperature, wind), so `hour_start_jst` (the
observation time minus one hour) lines up with `time_jst` of the traffic files: both are the start of the hour.

A day is uploaded when all 24 maps are there. A map the site does not serve (HTTP 404) inside the window is
retried on later runs; once the day is about to leave the window it is uploaded with the maps it has
(never empty), and the missing hours are printed."""
import argparse, csv, gzip, json, os, sys, tempfile, time, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import collect  # release helpers (gh, release_assets, ensure_release, upload)

JST = timezone(timedelta(hours=9))
BASE = "https://www.jma.go.jp/bosai/amedas"
WINDOW = 8        # days back from yesterday that the site still serves completely
NEAR_EXPIRY = 1   # the oldest days of the window are uploaded as they are
PAUSE = 0.5       # seconds between requests
# source element -> column. Every value comes with a quality flag (`<column>_aqc`, 0 = normal).
ELEMENTS = [
    ("temp", "temp_c"),
    ("humidity", "humidity_pct"),
    ("precipitation10m", "precip_10m_mm"),
    ("precipitation1h", "precip_1h_mm"),
    ("precipitation3h", "precip_3h_mm"),
    ("precipitation24h", "precip_24h_mm"),
    ("wind", "wind_ms"),
    ("windDirection", "wind_dir16"),
    ("sun10m", "sun_10m_min"),
    ("sun1h", "sun_1h_h"),
    ("snow", "snow_depth_cm"),
    ("snow1h", "snow_1h_cm"),
    ("snow6h", "snow_6h_cm"),
    ("snow12h", "snow_12h_cm"),
    ("snow24h", "snow_24h_cm"),
    ("pressure", "pressure_hpa"),
    ("normalPressure", "sea_level_pressure_hpa"),
    ("visibility", "visibility_m"),
    ("weather", "weather_code"),
]
KNOWN = dict(ELEMENTS)
COLUMNS = ["obs_time_jst", "hour_start_jst", "station_id"] + [c for _, col in ELEMENTS for c in (col, col + "_aqc")]
STATION_COLUMNS = ["station_id", "type", "elems", "lat", "lon", "alt_m", "name_ja", "name_kana", "name_en"]


def get(url, tries=3):
    """Parsed JSON, or None when the site answers 404 (the map is not there)."""
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "japan-road-traffic"}),
                                        timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            err = e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            err = e
        time.sleep(5 * (i + 1))
    raise RuntimeError(f"{url}: {err}")


def rows_of(obs, data):
    """One row per station of a map. A new element in the source stops the run (the columns are fixed)."""
    hour_start = (datetime.strptime(obs, "%Y%m%d%H%M") - timedelta(hours=1)).strftime("%Y%m%d%H%M")
    out = []
    for sid in sorted(data):
        unknown = set(data[sid]) - set(KNOWN)
        if unknown:
            raise ValueError(f"map {obs}: unknown elements {sorted(unknown)} at station {sid}")
        row = {"obs_time_jst": obs, "hour_start_jst": hour_start, "station_id": sid}
        for el, col in ELEMENTS:
            v = data[sid].get(el)
            if v is not None:
                if not isinstance(v, list) or len(v) != 2:
                    raise ValueError(f"map {obs}: station {sid} {el} = {v!r}, expected [value, flag]")
                row[col], row[col + "_aqc"] = v
        out.append(row)
    return out


def write_csv(path, rows, columns):
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="raise")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r[k]) for k in columns})


def station_rows(table):
    out = []
    for sid in sorted(table):
        s = table[sid]
        out.append({"station_id": sid, "type": s["type"], "elems": s["elems"],
                    # degrees and minutes in the source
                    "lat": round(s["lat"][0] + s["lat"][1] / 60, 5), "lon": round(s["lon"][0] + s["lon"][1] / 60, 5),
                    "alt_m": s["alt"], "name_ja": s["kjName"], "name_kana": s["knName"], "name_en": s["enName"]})
    return out


def main(argv=None, now=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="list what is missing, fetch nothing")
    a = ap.parse_args(argv)
    now = now or datetime.now(JST)
    # day D needs the map of D+1 00:00, served a few minutes after midnight
    last = (now - timedelta(days=1)).date() if now.hour >= 1 else (now - timedelta(days=2)).date()
    days = [last - timedelta(days=i) for i in range(WINDOW, -1, -1)]
    cache, failed, partial = {}, [], []

    for month in sorted({d.strftime("%Y%m") for d in days}):
        tag, name = f"amedas-{month}", f"amedas_stations_{month}.csv.gz"
        have = (collect.release_assets(tag) or set()) if a.dry_run else collect.ensure_release(tag, cache)
        if name in have:
            continue
        if a.dry_run:
            print(f"would fetch the station table for {month}"); continue
        rows = station_rows(get(f"{BASE}/const/amedastable.json"))
        if len(rows) < 1000:
            raise RuntimeError(f"station table has only {len(rows)} stations")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, name)
            write_csv(path, rows, STATION_COLUMNS)
            if not collect.upload(tag, path, cache):
                failed.append(name)
        print(f"station table {month}: {len(rows)} stations", flush=True)

    for d in days:
        day = d.strftime("%Y%m%d"); tag = f"amedas-{day[:6]}"; name = f"amedas_{day}.csv.gz"
        have = (collect.release_assets(tag) or set()) if a.dry_run else collect.ensure_release(tag, cache)
        if name in have:
            continue
        if a.dry_run:
            print(f"would fetch {day}"); continue
        rows, missing, counts = [], [], []
        for h in range(1, 25):
            t = datetime(d.year, d.month, d.day) + timedelta(hours=h)
            obs = t.strftime("%Y%m%d%H%M")
            data = get(f"{BASE}/data/map/{t:%Y%m%d%H}0000.json")
            time.sleep(PAUSE)
            if data is None:
                missing.append(obs); continue
            rows += rows_of(obs, data)
            counts.append(len(data))
        near_expiry = (last - d).days >= WINDOW - NEAR_EXPIRY
        if missing and not near_expiry:
            print(f"  {day}: {len(missing)} of 24 maps not served yet, retry later"); continue
        if not rows:
            print(f"  {day}: not kept (no maps; the site no longer has this day)"); continue
        if min(counts) < 0.9 * max(counts):
            print(f"::warning::{day}: stations per map range {min(counts)}..{max(counts)}")
        if missing:
            partial.append(f"{day} (missing {', '.join(m[8:] for m in missing)})")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, name)
            write_csv(path, rows, COLUMNS)
            if collect.upload(tag, path, cache):
                print(f"  {day}: {len(rows)} rows, {24 - len(missing)} maps", flush=True)
            else:
                failed.append(name)
    if partial:
        print("uploaded with maps the site no longer has: " + "; ".join(partial))
    if failed:
        print(f"::error::{len(failed)} file(s) failed: {failed}")
        sys.exit(1)


if __name__ == "__main__":
    main()
