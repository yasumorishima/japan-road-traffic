"""Write dataset-metadata.json (uploaded with every version) into the Kaggle build directory and
kaggle/settings.json (the column descriptions, kept to enter on the dataset page by hand: the upload sends
them in resources[].schema, but on the Earth Vital Signs dataset they did not appear on the page), from one
list of descriptions. Stops if a built file has a column that
is not described or lacks one that is, so the two cannot drift from the data.

  python scripts/make_kaggle_meta.py <build dir> [--settings PATH]"""
import argparse, json, os, re, sys
import pandas as pd
import pyarrow.parquet as pq

ROOT = os.path.join(os.path.dirname(__file__), "..")
ID = "yasunorim/japan-road-traffic-volume"
TITLE = "Japan Road Traffic Volume (Hourly Archive)"
SUBTITLE = "Vehicle counts on Japan's national highways, hourly nationwide and 5-min Kanto"
SOURCES = ("MLIT Traffic Volume API (交通量API, reference values), data provided by the Japan Road Traffic "
           "Information Center (JARTIC, https://www.jartic-open-traffic.org/); place names from the GSI reverse "
           "geocoder (国土地理院); census_2021_counters.csv from the 2021 Road Traffic Census (令和3年度全国道路・街路交通情勢調査, "
           "MLIT, 公共データ利用規約 第1.0版), edited. Collected three times a day and built by https://github.com/yasumorishima/japan-road-traffic, "
           "which uploads a day only when every check passes.")

DIR = {"up": "the 'up' direction (上り)", "down": "the 'down' direction (下り)"}
COLS = {
    "time_jst": "Start of the interval (the source's time code), Japan Standard Time (UTC+9), no time zone attached. "
                "Five-minute code 09:05 is 09:05-09:09 in the API specification",
    "counter_id": "Counter ID (常時観測点コード). The same ID can exist as a permanent counter and as a CCTV counter: "
                  "the key is (counter_id, sensor); join counters.csv on both",
    "sensor": "loop = permanent counter (loop or ultrasonic detector), cctv = AI count on CCTV images",
    "road_type": "1 = expressway (高速自動車国道) with an MLIT counter, 3 = national highway (一般国道)",
    "regional_bureau": "Regional bureau number (地方整備局等番号): 81 Hokkaido, 82 Tohoku, 83 Kanto, 84 Hokuriku, "
                       "85 Chubu, 86 Kinki, 87 Chugoku, 88 Shikoku, 89 Kyushu, 90 Okinawa",
    "sub_region": "Sub-region code (開発建設部／都道府県コード) as given by the source; empty outside Hokkaido and Chubu",
}
for d in ("up", "down"):
    w = DIR[d]
    COLS |= {
        f"{d}_total": f"All vehicles in the interval, {w}. CCTV counters only (the source gives it directly); empty for "
                      f"permanent counters, where it is {d}_small + {d}_large + {d}_unclassified",
        f"{d}_small": f"Small vehicles in the interval, {w}; empty when the source gives no value",
        f"{d}_large": f"Large vehicles in the interval, {w}; empty when the source gives no value",
        f"{d}_unclassified": f"Vehicles whose size could not be told, {w}",
        f"{d}_power_failure": f"Permanent counters: True when the source flags a power failure, {w}; empty for CCTV",
        f"{d}_loop_fault": f"Permanent counters: True when the source flags a loop detector fault, {w}; empty for CCTV",
        f"{d}_ultrasonic_fault": f"Permanent counters: True when the source flags an ultrasonic detector fault, {w}; "
                                 f"empty for CCTV",
        f"{d}_missing": f"Permanent counters: True when the source flags the value as missing (欠測), {w}; empty for CCTV",
        f"{d}_missing_processing": f"Hourly CCTV only: the source's 5分欠測処理フラグ, {w}. The API specification "
                                   f"defines 1 = five-minute processing and 2 = one hour; 0 also occurs and is not "
                                   f"defined there. Kept as the integer code",
    }
CAM = {
    "cam_preset_position": "camera is off its preset position (カメラプリセット位置); the source blanks the counts then",
    "cam_weather_degraded": "image degraded by weather (気象影響による映像不良)",
    "cam_low_light": "not enough light (照度不足)",
    "cam_incident": "incident such as a traffic accident (突発事象（交通事故等）)",
    "cam_server_status": "server not running (サーバの稼働)",
    "cam_feed_status": "video feed not received (カメラの映像受信)",
    "cam_decode_status": "video decoding failed (映像のデコード処理)",
    "cam_import_failure": "decoded video could not be passed to the analyser (取込加工処理の失敗)",
    "cam_analyzer_freeze": "the image analyser froze (映像解析機能のフリーズ)",
    "cam_other_error": "other error (その他エラー)",
}
for k, v in CAM.items():
    COLS[k] = f"Five-minute CCTV rows only: True = {v}, False = normal, empty = could not be judged or not a CCTV row"
# derived columns (added by build_kaggle.py after the source's columns)
for d in ("up", "down"):
    COLS[f"{d}_vehicles"] = (f"Derived: all vehicles in the interval, {DIR[d]}, one column for both sensors. Permanent "
                             f"counters: {d}_small + {d}_large + {d}_unclassified, left empty when any of the four "
                             f"{d}_* fault/missing flags is set (a failed counter reports 0 or partial counts with a "
                             f"flag). CCTV counters: {d}_total as given (hourly CCTV rows with {d}_missing_processing = 1 can rest on "
                             f"incomplete five-minute data). Empty when the source gives no count")
COLS |= {
    "vehicles": "Derived: up_vehicles + down_vehicles; empty when either is empty. Some counters count one direction "
                "only and report 0 for the other",
    "flagged": "Derived, permanent counters only: True when any of the eight fault/missing flags (both directions) is "
               "set, False when none is; empty for CCTV rows",
    "weekday": "Derived: day of the week of time_jst, 0 = Monday ... 6 = Sunday",
    "is_holiday": "Derived: True on Japanese national holidays and substitute holidays (国民の祝日・休日), from the "
                  "Cabinet Office list (内閣府「国民の祝日」CSV). Weekends are not marked; use weekday",
    "prefecture": "Derived from counters.csv: prefecture at the counter's location (Japanese); empty for the few "
                  "counters with no municipality in counters.csv (none found, or not resolved yet). Joining "
                  "counters.csv gives prefecture_x / prefecture_y: drop one first",
    "prefecture_en": "Derived: the same prefecture in English (Hokkaido, Tokyo, Osaka ...), from the JIS prefecture "
                     "code in counters.csv municipality_code",
}
COUNTER_COLS = {
    "counter_id": "Counter ID (常時観測点コード)",
    "sensor": "loop = permanent counter, cctv = AI count on CCTV images; (counter_id, sensor) is the key",
    "road_type": "1 = expressway, 3 = national highway",
    "longitude": "Longitude, WGS84, as given by the source",
    "latitude": "Latitude, WGS84, as given by the source",
    "prefecture": "Prefecture at the location (Japanese), from the GSI reverse geocoder",
    "prefecture_en": "The same prefecture in English (Hokkaido, Tokyo, Osaka ...)",
    "municipality_code": "Five-digit local government code (全国地方公共団体コード without the check digit) with its "
                         "leading zero (01337); read it as text, e.g. pandas dtype={'municipality_code': str}",
    "municipality": "City, ward, town or village at the location (Japanese)",
    "municipality_en": "The same municipality in English, e.g. Yokohama-shi Tsurumi-ku, Nanae-cho (-shi city, -ku ward, "
                       "-cho/-machi town, -mura/-son village): the name from Wikidata's English label without macrons, "
                       "the type from the official reading in the MIC local government code list. Not unique (two "
                       "towns can share a name): join on municipality_code",
    "town": "Town or district name at the location (Japanese only: there is no official reading to romanise), from "
            "the GSI reverse geocoder; can be empty",
    "in_5min_box": "True when the counter is inside the Kanto box that has five-minute data "
                   "(longitude 138.4-140.95, latitude 34.85-37.2)",
    "last_seen": "Latest day (JST) this counter reported in the API or in the archive",
}

CENSUS_DESC = ("365 permanent counters on national highways tied to their section of the 2021 Road Traffic Census "
               "(道路交通センサス, MLIT): route, lanes, speed limit and 2021 autumn weekday travel speeds. Join on "
               "(counter_id, sensor). The tie is inferred, not given by either source: see the match_ columns.")
SPEED = ("2021 autumn weekday average travel speed, km/h, {when}, {d} (census, from ETC2.0 probe data); "
         "empty where the census did not measure it. A static 2021 value, not today's speed")
RUSH = "rush hours (7-9h or 17-19h, whichever is congested)"
CENSUS_COLS = {
    "counter_id": "Counter ID (常時観測点コード); join with sensor",
    "sensor": "Always loop (permanent counter)",
    "census_section": "Census section number (交通調査基本区間番号), 11 digits with its leading zero; read it as text",
    "route_number": "National route number (一般国道 N 号); empty for a route the census numbers outside 1-507",
    "route_ja": "Route name in the census (Japanese), with the bypass or road name in brackets when it has one",
    "route_en": "Route in English, e.g. National Route 5",
    "observation_address_ja": "Address of the census observation point (Japanese)",
    "observation_month": "Month of the census observation (YYYY-MM; the road manager's permanent counter, several days)",
    "census_vol24_2021": ("Census 24-hour volume, both directions, vehicles: observed in autumn 2021 (for 2 sections observed for 12 hours "
                          "and expanded by the census; for 1 section the census estimate from a 2020 survey)"),
    "large12_pct_2021": "Census daytime (7-19h) share of large vehicles, %",
    "lanes": "Number of lanes (census)",
    "speed_limit_kmh": "Posted speed limit, km/h (census)",
    "peak_speed_up_kmh": SPEED.format(when=RUSH, d=DIR["up"]),
    "peak_speed_down_kmh": SPEED.format(when=RUSH, d=DIR["down"]),
    "offpeak_speed_up_kmh": SPEED.format(when="daytime off-peak (9-17h)", d=DIR["up"]),
    "offpeak_speed_down_kmh": SPEED.format(when="daytime off-peak (9-17h)", d=DIR["down"]),
    "match_km": "Distance, km, between the counter and the geocoded census observation address (resolved to the town or a finer level; a pair resolved only to the municipality was left out)",
    "town_in_address": "True when the counter's town name appears in the census observation address",
    "match_vol_ratio": "Counter's September 2026 weekday median 24-hour volume / census 2021 volume; used to choose "
                       "the section (kept between 0.86 and 1.16), not a measure of traffic growth",
}

FILE_DESC = {
    "hourly": "Hourly vehicle counts for {m} (JST), nationwide: permanent and CCTV counters on national highways and "
              "the expressways that have MLIT counters. One row per hour, counter and sensor.",
    "five_minute_kanto": "Five-minute vehicle counts for {m} (JST) inside the Kanto box (Tokyo, Kanagawa, Saitama, "
                         "Chiba, Ibaraki, Tochigi, Gunma and edges): permanent and CCTV counters. One row per "
                         "five minutes, counter and sensor.",
}
DESCRIPTION = open(os.path.join(ROOT, "kaggle", "description.md"), encoding="utf-8").read().strip()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out"); ap.add_argument("--settings", default=os.path.join(ROOT, "kaggle", "settings.json"))
    a = ap.parse_args()
    out = a.out
    resources, files = [], {}
    names = sorted(os.listdir(out))
    for n in names:
        p = os.path.join(out, n)
        m = re.match(r"^(hourly|five_minute_kanto)_(\d{4}-\d{2})_(\d{2})-(\d{2})\.parquet$", n)
        if m:
            cols = pq.read_schema(p).names
            desc = FILE_DESC[m.group(1)].format(m=f"{m.group(2)}-{m.group(3)} to {m.group(2)}-{m.group(4)}")
            table = COLS
        elif n == "counters.csv":
            cols = pd.read_csv(p, nrows=0).columns.tolist()
            desc = ("One row per counter (ID and sensor) seen in the API or the archive: location, prefecture, "
                    "municipality and town (GSI reverse geocoder), and the last day it reported.")
            table = COUNTER_COLS
        elif n == "census_2021_counters.csv":
            cols = pd.read_csv(p, nrows=0).columns.tolist()
            desc = CENSUS_DESC
            table = CENSUS_COLS
        elif n == "dataset-metadata.json":
            continue
        else:
            sys.exit(f"unexpected file in the build: {n}")
        missing = [c for c in cols if c not in table]
        if missing:
            sys.exit(f"{n}: columns without a description: {missing}")
        if table is not COLS and set(cols) != set(table):
            sys.exit(f"{n}: described columns not in the file: {sorted(set(table) - set(cols))}")
        resources.append({"path": n, "description": desc,
                          "schema": {"fields": [{"name": c, "description": table[c]} for c in cols]}})
        files[n] = {"description": desc, "columns": {c: table[c] for c in cols}}
    if not any(n.startswith("hourly_") for n in files) or not {"counters.csv", "census_2021_counters.csv"} <= set(files):
        sys.exit("the build has no hourly file, no counters.csv or no census_2021_counters.csv")
    meta = {"title": TITLE, "subtitle": SUBTITLE, "id": ID, "licenses": [{"name": "CC-BY-4.0"}],
            "keywords": ["transportation", "time series analysis", "japan", "automobiles and vehicles", "tabular"],
            "expectedUpdateFrequency": "daily", "description": DESCRIPTION, "resources": resources}
    json.dump(meta, open(os.path.join(out, "dataset-metadata.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    settings = {"id": ID, "expectedUpdateFrequency": "daily", "userSpecifiedSources": SOURCES, "files": files}
    with open(a.settings, "w", encoding="utf-8", newline="\n") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2); f.write("\n")
    print(f"metadata for {len(resources)} files")

if __name__ == "__main__":
    main()
