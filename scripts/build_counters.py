"""Build data/counters.csv: one row per counter (ID + sensor type) with its location and the prefecture,
municipality and town at that location (GSI reverse geocoder). The traffic API gives no road or place names.

Counters come from a few hourly time codes of a recent day for every layer and, with --from-files, from
archived day files (release assets), so a counter that has stopped reporting keeps its row. Rows already in
counters.csv are kept; last_seen is the latest day a counter was seen. Place names are cached in
data/geocode_cache.json so a rebuild only asks GSI about new locations."""
import argparse, csv, glob, gzip, json, os, re, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import fetch

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "data", "counters.csv")
CACHE = os.path.join(ROOT, "data", "geocode_cache.json")
GEOCODER = "https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress?lat={lat}&lon={lon}"
MUNI = "https://maps.gsi.go.jp/js/muni.js"
LAYERS = [("t_travospublic_measure_1h", "loop"), ("t_travospublic_measure_1h_img", "cctv")]
HOURS = ("0000", "0800", "1600")
HEADER = ["counter_id", "sensor", "road_type", "longitude", "latitude", "prefecture",
          "municipality_code", "municipality", "town", "in_5min_box", "last_seen"]
ASSET = re.compile(r"^(loop|cctv)_(5m|1h)_road([13])_(\d{8})\.csv\.gz$")

def municipalities():
    text = urllib.request.urlopen(MUNI, timeout=60).read().decode("utf-8")
    out = {}
    for code, val in re.findall(r'MUNI_ARRAY\["(\d+)"\]\s*=\s*\'([^\']*)\'', text):
        pref_code, pref, _, name = val.split(",")
        out[int(code)] = (pref, name.replace("　", " "))
    if len(out) < 1500:
        sys.exit(f"municipality table looks wrong: {len(out)} entries")
    return out

def geocode(lat, lon):
    for attempt in range(3):
        try:
            return json.load(urllib.request.urlopen(GEOCODER.format(lat=lat, lon=lon), timeout=60)).get("results") or {}
        except Exception as e:
            print(f"  geocoder retry {attempt} ({lat},{lon}): {e}", flush=True)
            time.sleep(5 * 2 ** attempt)
    return None  # not cached: asked again on the next build

def from_api(day, seen):
    for layer, sensor in LAYERS:
        for road in (3, 1):
            for h in HOURS:
                feats = fetch.get(layer, road, day + h, day + h, fetch.JAPAN)
                if feats is None:
                    sys.exit(f"{layer} {day}{h} too large for one request")
                for f in feats:
                    p = f["properties"]; lon, lat = f["geometry"]["coordinates"][0]
                    note(seen, p["常時観測点コード"], sensor, p["道路種別"], lon, lat, day)

def from_files(root, seen):
    for p in sorted(glob.glob(os.path.join(root, "**", "*.csv.gz"), recursive=True)):
        m = ASSET.match(os.path.basename(p))
        if not m:
            continue
        sensor, _, road, day = m.groups()
        with gzip.open(p, "rt", encoding="utf-8") as g:
            for r in csv.DictReader(g):
                note(seen, r["常時観測点コード"], sensor, r["道路種別"], r["経度"], r["緯度"], day)

def note(seen, cid, sensor, road, lon, lat, day):
    k = (int(cid), sensor)
    if k not in seen or day >= seen[k][3]:
        seen[k] = (int(road), round(float(lon), 7), round(float(lat), 7), day)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-files", help="directory with downloaded release assets to take counters from")
    ap.add_argument("--no-api", action="store_true", help="only use --from-files")
    a = ap.parse_args()
    seen = {}
    if not a.no_api:
        day = (datetime.now(timezone(timedelta(hours=9))) - timedelta(days=2)).strftime("%Y%m%d")
        from_api(day, seen)
        if len(seen) < 1000:
            sys.exit(f"only {len(seen)} counters from the API; the source may be partly down, not writing")
    if a.from_files:
        from_files(a.from_files, seen)
    old = {}
    if os.path.exists(OUT):
        for r in csv.DictReader(open(OUT, encoding="utf-8")):
            old[(int(r["counter_id"]), r["sensor"])] = r
    muni = municipalities()
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    cache = {k: v for k, v in cache.items() if v}  # empty answers are asked again
    rows, unknown, asked = [], 0, 0
    keys = sorted(set(seen) | set(old))
    for cid, sensor in keys:
        if (cid, sensor) in seen:
            road, lon, lat, day = seen[(cid, sensor)]
            last = f"{day[:4]}-{day[4:6]}-{day[6:]}"
            prev = old.get((cid, sensor))
            if prev and prev.get("last_seen", "") > last:
                last = prev["last_seen"]
        else:  # in the table but not seen in this build: keep the row as it was
            r = old[(cid, sensor)]
            road, lon, lat, last = int(r["road_type"]), float(r["longitude"]), float(r["latitude"]), r.get("last_seen", "")
        k = f"{lat},{lon}"
        if k not in cache:
            g = geocode(lat, lon); asked += 1; time.sleep(0.5)
            if g:
                cache[k] = g
            if asked % 50 == 0:
                json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=0, sort_keys=True)
        g = cache.get(k, {})
        code = int(g["muniCd"]) if g.get("muniCd") else None
        pref, name = muni.get(code, ("", "")) if code else ("", "")
        if not name:
            unknown += 1
        rows.append([cid, sensor, road, lon, lat, pref, f"{code:05d}" if code else "", name, g.get("lv01Nm", ""),
                     fetch.KANTO[0] <= lon <= fetch.KANTO[2] and fetch.KANTO[1] <= lat <= fetch.KANTO[3], last])
    if unknown > 0.05 * len(rows):
        sys.exit(f"{unknown} of {len(rows)} counters got no municipality; not writing")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=0, sort_keys=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(HEADER)
        w.writerows(rows)
    print(f"{len(rows)} counters ({len(rows) - len(old)} new, {unknown} without a municipality) -> {OUT}")

if __name__ == "__main__":
    main()
