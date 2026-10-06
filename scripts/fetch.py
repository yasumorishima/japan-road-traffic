"""Fetch MLIT/JARTIC traffic volume (WFS) into gzip CSV, one file per layer/road-type/JST day.
Polite: one request at a time, pause between requests, split time ranges that exceed the 6 MB response cap."""
import csv, gzip, json, os, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta

API = "https://api.jartic-open-traffic.org/geoserver"
JAPAN = (122, 20, 154, 46)
KANTO = (138.4, 34.85, 140.95, 37.2)
KEY = ["時間コード", "常時観測点コード", "道路種別", "地方整備局等番号", "開発建設部／都道府県コード"]
# Each layer has its own field names (API specification forms 1-4, checked against live responses 2026-10-06).
LOOP = KEY + ["上り・小型交通量", "上り・大型交通量", "上り・車種判別不能交通量",
              "上り・停電", "上り・ループ異常", "上り・超音波異常", "上り・欠測",
              "下り・小型交通量", "下り・大型交通量", "下り・車種判別不能交通量",
              "下り・停電", "下り・ループ異常", "下り・超音波異常", "下り・欠測"]
CCTV_1H = KEY + ["上り・自動車交通量", "上り・小型交通量", "上り・大型交通量", "上り・小型大型判別不能交通量",
                 "上り・5分欠測処理フラグ",
                 "下り・自動車交通量", "下り・小型交通量", "下り・大型交通量", "下り・小型大型判別不能交通量",
                 "下り・5分欠測処理フラグ"]
CCTV_5M = KEY + ["上り・自動車交通量（集計値）", "上り・小型交通量（集計値）", "上り・大型交通量（集計値）",
                 "上り・小型大型判別不能交通量（集計値）",
                 "下り・自動車交通量（集計値）", "下り・小型交通量（集計値）", "下り・大型交通量（集計値）",
                 "下り・小型大型判別不能交通量（集計値）",
                 "カメラプリセット位置", "気象影響による映像不良", "照度不足", "突発事象（交通事故等）", "サーバの稼働",
                 "カメラの映像受信", "映像のデコード処理", "デコード映像から映像解析機能への取込加工処理の失敗",
                 "映像解析機能のフリーズ", "その他エラー"]
SCHEMA = {"t_travospublic_measure_5m": LOOP, "t_travospublic_measure_1h": LOOP,
          "t_travospublic_measure_1h_img": CCTV_1H, "t_travospublic_measure_5m_img": CCTV_5M}
# Fields not kept because the time code already holds them.
REDUNDANT = {"収集時間フラグ（5分間／1時間）", "観測年月日", "時間帯"}
PAUSE = 2.0

def columns(layer):
    return SCHEMA[layer] + ["経度", "緯度"]

def row(layer, f):
    """One CSV row. Fails when the source adds a field or drops one: a renamed field must never turn into
    an empty column without anyone noticing (it did once, for every CCTV file)."""
    p = f["properties"]
    unknown = set(p) - set(SCHEMA[layer]) - REDUNDANT
    absent = set(SCHEMA[layer]) - set(p)
    if unknown or absent:
        raise RuntimeError(f"{layer}: schema changed (new {sorted(unknown)}, missing {sorted(absent)})")
    lon, lat = f["geometry"]["coordinates"][0]
    return ["" if p[c] is None else p[c] for c in SCHEMA[layer]] + [lon, lat]

def get(layer, road, t0, t1, bbox):
    cql = f"道路種別={road} AND 時間コード>={t0} AND 時間コード<={t1} AND BBOX(ジオメトリ,{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]},'EPSG:4326')"
    q = urllib.parse.urlencode({"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": layer,
                                "srsName": "EPSG:4326", "outputFormat": "application/json",
                                "exceptions": "application/json", "cql_filter": cql})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(f"{API}?{q}", timeout=300) as r:
                d = json.load(r)
        except Exception as e:
            print(f"  retry {attempt} {layer} {road} {t0}-{t1}: {e}", flush=True)
            time.sleep(10 * 2 ** attempt)
            continue
        finally:
            time.sleep(PAUSE)
        if "features" in d:
            m, n = d.get("numberMatched"), d.get("numberReturned")
            if isinstance(m, int) and isinstance(n, int) and m != n:
                return None  # a server-side feature cap cut the response: split like an oversized one
            return d["features"]
        if d.get("errorType") in ("Function.ResponseSizeTooLarge", "Runtime.OutOfMemory"):
            return None  # caller splits the range
        print(f"  error {layer} {road} {t0}-{t1}: {str(d)[:200]}", flush=True)
        time.sleep(10 * 2 ** attempt)
    raise RuntimeError(f"gave up {layer} {road} {t0}-{t1}")

def codes(day, step):
    t = datetime.strptime(day, "%Y%m%d")
    return [(t + timedelta(minutes=m)).strftime("%Y%m%d%H%M") for m in range(0, 1440, step)]

def fetch_codes(layer, road, cs, bbox, out):
    feats = get(layer, road, cs[0], cs[-1], bbox) if len(cs) > 1 else get(layer, road, cs[0], cs[0], bbox)
    if feats is None:
        if len(cs) == 1:
            raise RuntimeError(f"single code too large {layer} {cs[0]}")
        h = len(cs) // 2
        fetch_codes(layer, road, cs[:h], bbox, out); fetch_codes(layer, road, cs[h:], bbox, out)
        return
    for f in feats:
        out.append(row(layer, f))

def write_csv(path, rows, layer):
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", newline="", encoding="utf-8") as g:
        w = csv.writer(g); w.writerow(columns(layer)); w.writerows(rows)
    os.replace(tmp, path)

def day_file(root, layer, road, day):
    return os.path.join(root, layer, f"road{road}", day[:6], f"{day}.csv.gz")

def fetch_day(root, layer, road, day, bbox, step, chunk):
    path = day_file(root, layer, road, day)
    if os.path.exists(path):
        return
    cs = codes(day, step); rows = []
    for i in range(0, len(cs), chunk):
        fetch_codes(layer, road, cs[i:i + chunk], bbox, rows)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_csv(path, rows, layer)
    print(f"{layer} road{road} {day}: {len(rows)} rows", flush=True)

if __name__ == "__main__":
    root, kind, d0, d1 = sys.argv[1:5]
    days = []
    t = datetime.strptime(d0, "%Y%m%d")
    while t <= datetime.strptime(d1, "%Y%m%d"):
        days.append(t.strftime("%Y%m%d")); t += timedelta(days=1)
    for day in days:
        for road in (3, 1):
            if kind == "1h":
                for layer in ("t_travospublic_measure_1h", "t_travospublic_measure_1h_img"):
                    fetch_day(root, layer, road, day, JAPAN, 60, 4)
            else:
                for layer in ("t_travospublic_measure_5m", "t_travospublic_measure_5m_img"):
                    fetch_day(root, layer, road, day, KANTO, 5, 12)
