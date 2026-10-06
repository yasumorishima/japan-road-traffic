"""Build data/counters.csv: one row per counter (ID + sensor type) with its location and the prefecture,
municipality and town at that location (GSI reverse geocoder). The traffic API gives no road or place names.

Counters are taken from a few hourly time codes of a recent day for every layer, so a counter that reported
in none of them is missing until the next build. Place names are cached in data/geocode_cache.json so a
rebuild only asks GSI about new locations."""
import csv, json, os, re, sys, time, urllib.request
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

def municipalities():
    text = urllib.request.urlopen(MUNI, timeout=60).read().decode("utf-8")
    out = {}
    for code, val in re.findall(r'MUNI_ARRAY\["(\d+)"\]\s*=\s*\'([^\']*)\'', text):
        pref_code, pref, _, name = val.split(",")
        out[int(code)] = (pref, name.replace("　", " "))
    if len(out) < 1500:
        sys.exit(f"municipality table looks wrong: {len(out)} entries")
    return out

def main():
    day = (datetime.now(timezone(timedelta(hours=9))) - timedelta(days=2)).strftime("%Y%m%d")
    counters = {}
    for layer, sensor in LAYERS:
        for road in (3, 1):
            for h in HOURS:
                feats = fetch.get(layer, road, day + h, day + h, fetch.JAPAN)
                if feats is None:
                    sys.exit(f"{layer} {day}{h} too large for one request")
                for f in feats:
                    p = f["properties"]; lon, lat = f["geometry"]["coordinates"][0]
                    counters[(p["常時観測点コード"], sensor)] = (int(p["道路種別"]), round(lon, 7), round(lat, 7))
    if len(counters) < 1000:
        sys.exit(f"only {len(counters)} counters; the source may be partly down, not writing")
    muni = municipalities()
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    rows, unknown = [], 0
    for (cid, sensor), (road, lon, lat) in sorted(counters.items()):
        k = f"{lat},{lon}"
        if k not in cache:
            r = json.load(urllib.request.urlopen(GEOCODER.format(lat=lat, lon=lon), timeout=60))
            cache[k] = r.get("results") or {}
            time.sleep(0.5)
        g = cache[k]
        code = int(g["muniCd"]) if g.get("muniCd") else None
        pref, name = muni.get(code, ("", "")) if code else ("", "")
        if not name:
            unknown += 1
        rows.append([cid, sensor, road, lon, lat, pref, code or "", name, g.get("lv01Nm", ""),
                     fetch.KANTO[0] <= lon <= fetch.KANTO[2] and fetch.KANTO[1] <= lat <= fetch.KANTO[3]])
    if unknown > 0.05 * len(rows):
        sys.exit(f"{unknown} of {len(rows)} counters got no municipality; not writing")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=0, sort_keys=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["counter_id", "sensor", "road_type", "longitude", "latitude", "prefecture",
                    "municipality_code", "municipality", "town", "in_5min_box"])
        w.writerows(rows)
    print(f"{len(rows)} counters from {day} ({unknown} without a municipality) -> {OUT}")

if __name__ == "__main__":
    main()
