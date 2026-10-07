"""Turn the daily release files into the Kaggle layout: one Parquet file per ten days (days 1-10, 11-20 and 21 to
the end of the month) and resolution, English column names, JST timestamps. Input: a directory holding the
downloaded release assets (any depth).

  hourly_YYYY-MM_DD-DD.parquet             nationwide, hourly (permanent and CCTV counters, national highways and expressways)
  five_minute_kanto_YYYY-MM_DD-DD.parquet  Kanto box, every 5 minutes
Files are flat (no folders) so the upload does not depend on how Kaggle unpacks folders. Kaggle's file preview
failed on monthly files of 1.1 million rows and more and worked up to 900,000 (measured 2026-10-07); ten days
are at most about 0.6 million rows. The name of a chunk stays the same while its days fill in, so descriptions
entered on the dataset page carry over to later versions.
  counters.csv                    one row per counter with location and place names"""
import argparse, calendar, glob, os, re, shutil, sys
import pandas as pd

KEY = {"時間コード": "time_code", "常時観測点コード": "counter_id", "道路種別": "road_type",
       "地方整備局等番号": "regional_bureau", "開発建設部／都道府県コード": "sub_region"}
# Field names differ by layer (permanent counters / CCTV hourly / CCTV 5-minute); they map onto one table.
LOOP = {"上り・小型交通量": "up_small", "上り・大型交通量": "up_large", "上り・車種判別不能交通量": "up_unclassified",
        "上り・停電": "up_power_failure", "上り・ループ異常": "up_loop_fault", "上り・超音波異常": "up_ultrasonic_fault",
        "上り・欠測": "up_missing",
        "下り・小型交通量": "down_small", "下り・大型交通量": "down_large", "下り・車種判別不能交通量": "down_unclassified",
        "下り・停電": "down_power_failure", "下り・ループ異常": "down_loop_fault",
        "下り・超音波異常": "down_ultrasonic_fault", "下り・欠測": "down_missing"}
CCTV_1H = {"上り・自動車交通量": "up_total", "上り・小型交通量": "up_small", "上り・大型交通量": "up_large",
           "上り・小型大型判別不能交通量": "up_unclassified", "上り・5分欠測処理フラグ": "up_missing_processing",
           "下り・自動車交通量": "down_total", "下り・小型交通量": "down_small", "下り・大型交通量": "down_large",
           "下り・小型大型判別不能交通量": "down_unclassified", "下り・5分欠測処理フラグ": "down_missing_processing"}
CCTV_5M = {"上り・自動車交通量（集計値）": "up_total", "上り・小型交通量（集計値）": "up_small",
           "上り・大型交通量（集計値）": "up_large", "上り・小型大型判別不能交通量（集計値）": "up_unclassified",
           "下り・自動車交通量（集計値）": "down_total", "下り・小型交通量（集計値）": "down_small",
           "下り・大型交通量（集計値）": "down_large", "下り・小型大型判別不能交通量（集計値）": "down_unclassified",
           "カメラプリセット位置": "cam_preset_position", "気象影響による映像不良": "cam_weather_degraded",
           "照度不足": "cam_low_light", "突発事象（交通事故等）": "cam_incident", "サーバの稼働": "cam_server_status",
           "カメラの映像受信": "cam_feed_status", "映像のデコード処理": "cam_decode_status",
           "デコード映像から映像解析機能への取込加工処理の失敗": "cam_import_failure",
           "映像解析機能のフリーズ": "cam_analyzer_freeze", "その他エラー": "cam_other_error"}
SCHEMA = {("loop", "5m"): LOOP, ("loop", "1h"): LOOP, ("cctv", "1h"): CCTV_1H, ("cctv", "5m"): CCTV_5M}
NAME = re.compile(r"^(loop|cctv)_(5m|1h)_road([13])_(\d{8})\.csv\.gz$")
COUNTS = ["up_total", "up_small", "up_large", "up_unclassified", "down_total", "down_small", "down_large",
          "down_unclassified"]
FLAGS = ["up_power_failure", "up_loop_fault", "up_ultrasonic_fault", "up_missing",
         "down_power_failure", "down_loop_fault", "down_ultrasonic_fault", "down_missing"]
# hourly CCTV: API spec "1: 5-minute processing, 2: 1 hour"; 0 also occurs (not defined in the spec)
CODES = ["up_missing_processing", "down_missing_processing"]
STATUS = [v for v in CCTV_5M.values() if v.startswith("cam_")]
ORDER = (["time_jst", "counter_id", "sensor", "road_type", "regional_bureau", "sub_region"] + COUNTS + FLAGS + CODES)

def read(path, sensor, res):
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    mapping = dict(KEY, **SCHEMA[(sensor, res)], **{"経度": None, "緯度": None})
    if set(raw.columns) != set(mapping):
        sys.exit(f"{os.path.basename(path)}: columns {sorted(set(raw.columns) ^ set(mapping))} do not match the layer")
    df = raw.drop(columns=["経度", "緯度"]).rename(columns=mapping)  # locations live in counters.csv
    df["time_jst"] = pd.to_datetime(df.pop("time_code"), format="%Y%m%d%H%M")
    df["sensor"] = sensor
    for c in ["counter_id", "road_type", "regional_bureau"]:
        df[c] = pd.to_numeric(df[c]).astype("int32")
    df["sub_region"] = df["sub_region"].replace("", None)
    for c in COUNTS:
        df[c] = pd.to_numeric(df[c].replace("", None)).astype("Int32") if c in df else pd.array([None] * len(df), "Int32")
    for c in FLAGS:
        if c in df:
            bad = set(df[c]) - {"0", "1", ""}
            if bad:
                sys.exit(f"{os.path.basename(path)}: unexpected values {sorted(bad)} in {c}")
            df[c] = df[c].map({"0": False, "1": True, "": None}).astype("boolean")
        else:
            df[c] = pd.array([None] * len(df), "boolean")
    for c in CODES:
        if c in df:
            bad = set(df[c]) - {"0", "1", "2", ""}
            if bad:
                sys.exit(f"{os.path.basename(path)}: unexpected values {sorted(bad)} in {c}")
            df[c] = pd.to_numeric(df[c].replace("", None)).astype("Int8")
        else:
            df[c] = pd.array([None] * len(df), "Int8")
    # camera status (5-minute CCTV): 0 normal, 1 abnormal, blank = could not be judged -> True means abnormal
    for c in STATUS:
        if c in df:
            bad = set(df[c]) - {"0", "1", ""}
            if bad:
                sys.exit(f"{os.path.basename(path)}: unexpected values {sorted(bad)} in {c}")
            df[c] = df[c].map({"0": False, "1": True, "": None}).astype("boolean")
    cols = ORDER + (STATUS if (sensor, res) == ("cctv", "5m") else [])
    return df[cols]

def chunk(day):
    """YYYYMMDD -> 'YYYY-MM_01-10', 'YYYY-MM_11-20' or 'YYYY-MM_21-<last day of the month>'."""
    y, m, d = int(day[:4]), int(day[4:6]), int(day[6:])
    a, b = (1, 10) if d <= 10 else (11, 20) if d <= 20 else (21, calendar.monthrange(y, m)[1])
    return f"{y:04d}-{m:02d}_{a:02d}-{b:02d}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("out"); ap.add_argument("--counters", required=True)
    a = ap.parse_args()
    groups = {}
    for p in glob.glob(os.path.join(a.src, "**", "*.csv.gz"), recursive=True):
        m = NAME.match(os.path.basename(p))
        if m:
            sensor, res, road, day = m.groups()
            groups.setdefault((res, chunk(day)), []).append((p, sensor))
    if not groups:
        sys.exit("no release files found")
    os.makedirs(a.out, exist_ok=True)
    known = set(pd.read_csv(a.counters, usecols=["counter_id", "sensor"]).itertuples(index=False, name=None))
    unknown = set()
    for (res, month), files in sorted(groups.items()):
        parts = [read(p, s, res) for p, s in sorted(files)]
        df = pd.concat(parts, ignore_index=True)
        if res == "5m":  # camera status columns exist only for CCTV rows; keep one schema per file
            for c in STATUS:
                df[c] = df[c].astype("boolean") if c in df else pd.array([None] * len(df), "boolean")
            df = df[ORDER + STATUS]
        df = df.sort_values(["time_jst", "counter_id", "sensor"], kind="stable").reset_index(drop=True)
        dup = df.duplicated(["time_jst", "counter_id", "sensor"]).sum()
        if dup:
            sys.exit(f"{res} {month}: {dup} duplicated (time, counter, sensor) rows")
        unknown |= set(df[["counter_id", "sensor"]].drop_duplicates().itertuples(index=False, name=None)) - known
        name = ("hourly" if res == "1h" else "five_minute_kanto") + f"_{month}.parquet"  # month = 'YYYY-MM_DD-DD'
        df.to_parquet(os.path.join(a.out, name), index=False, compression="zstd", row_group_size=50_000)
        print(f"{name}: {len(df):,} rows, {df.time_jst.dt.date.nunique()} days", flush=True)
    if unknown:
        sys.exit(f"{len(unknown)} counters are not in {a.counters} (rebuild it with --from-files): {sorted(unknown)[:10]}")
    shutil.copyfile(a.counters, os.path.join(a.out, "counters.csv"))

if __name__ == "__main__":
    main()
