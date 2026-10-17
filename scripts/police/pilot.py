"""Pilot: measure one month of JARTIC "断面交通量情報" (police vehicle detectors, general roads).

Downloads each regional zip from the official JARTIC open-data page one at a time,
converts Shift-JIS to UTF-8 with iconv, and measures with polars:
rows, points, time coverage, duplicates, non-numeric counts, how often the point name
carries a route number and a direction, and how big an hourly parquet would be.
Nothing is published; the numbers go to the job log.

Source: 「断面交通量情報」（公益財団法人日本道路交通情報センター）https://www.jartic.or.jp/service/opendata/
"""
import json, os, re, subprocess, sys, time, urllib.request, zipfile
import polars as pl

BASE = "https://www.jartic.or.jp/d/opendata"
WORK = os.environ.get("WORK", "/tmp/police")
ONLY = [s for s in os.environ.get("ONLY", "").split(",") if s]

ROUTE = r"(?:[ＲR]|国道)\s*[0-9０-９]+"
DIRECTION = r"[東西南北]入|[東西南北]行|上り|下り|上|下|北進|南進|東進|西進|北行|南行|東行|西行|内回り|外回り|内|外"


def get(url, path):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=600) as r, open(path, "wb") as f:
                while True:
                    b = r.read(1 << 22)
                    if not b:
                        break
                    f.write(b)
            return
        except Exception as e:  # network hiccup: wait and retry a few times, then fail
            print("retry", url, e, file=sys.stderr)
            time.sleep(30 * (attempt + 1))
    raise SystemExit(f"download failed: {url}")


def measure(region, zpath):
    csv = os.path.join(WORK, f"{region}.csv")
    z = zipfile.ZipFile(zpath)
    members = [i for i in z.infolist() if i.filename.lower().endswith(".csv")]
    if not members:
        raise SystemExit(f"{region}: no csv in zip")
    header = None
    tmp, conv = csv + ".raw", csv + ".utf8"
    with open(csv, "wb") as out:
        for m in members:
            with z.open(m) as src, open(tmp, "wb") as raw:
                while True:
                    b = src.read(1 << 22)
                    if not b:
                        break
                    raw.write(b)
            with open(tmp, "rb") as raw, open(conv, "wb") as dst:
                subprocess.run(["iconv", "-f", "CP932", "-t", "UTF-8"], stdin=raw, stdout=dst, check=True)
            os.remove(tmp)
            with open(conv, "rb") as src:
                h = src.readline().decode().strip("\r\n")
                if header is None:
                    header = h
                    out.write((h + "\n").encode())
                elif h != header:
                    raise SystemExit(f"{region}: header differs between files: {h!r} vs {header!r}")
                while True:
                    b = src.read(1 << 22)
                    if not b:
                        break
                    out.write(b)
            os.remove(conv)
    os.remove(zpath)
    cols = header.split(",")
    lf = pl.scan_csv(csv, infer_schema=False, encoding="utf8")
    lf = lf.with_columns(pl.col(c).str.strip_chars() for c in cols)
    t = pl.col("時刻").str.strptime(pl.Datetime, "%Y/%m/%d %H:%M", strict=False)
    v = pl.col("断面交通量").cast(pl.Int64, strict=False)
    lf = lf.with_columns(t.alias("ts"), v.alias("vol"), (pl.col("情報源コード") + "_" + pl.col("計測地点番号")).alias("pid"))
    s = lf.select(
        pl.len().alias("rows"),
        pl.col("pid").n_unique().alias("points"),
        pl.col("ts").null_count().alias("bad_time"),
        pl.col("ts").min().alias("tmin"),
        pl.col("ts").max().alias("tmax"),
        pl.col("ts").n_unique().alias("n_times"),
        (pl.col("vol").is_null() & pl.col("断面交通量").is_not_null() & (pl.col("断面交通量") != "")).sum().alias("nonnum"),
        pl.col("断面交通量").is_null().sum().alias("empty"),
        (pl.col("vol") < 0).sum().alias("neg"),
        pl.col("vol").max().alias("vmax"),
        pl.col("vol").mean().alias("vmean"),
        (pl.col("計測地点番号") == "0").sum().alias("pt0_rows"),
    ).collect().to_dicts()[0]
    s["dup_point_time"] = s["rows"] - lf.select(pl.struct("pid", "ts").n_unique()).collect().item()
    nz = lf.filter(pl.col("計測地点番号") != "0")
    s["dup_point_time_excl0"] = nz.select(pl.len()).collect().item() - nz.select(pl.struct("pid", "ts").n_unique()).collect().item()
    per = lf.group_by("pid").agg(pl.col("ts").n_unique().alias("n")).collect()
    expect = s["n_times"]
    s["points_full"] = int((per["n"] >= expect).sum())
    s["points_ge90"] = int((per["n"] >= 0.9 * expect).sum())
    s["slot_coverage"] = round(float(per["n"].sum()) / (len(per) * expect), 4) if len(per) else None
    pts = lf.group_by("pid").agg(
        pl.col("計測地点名称").unique().alias("names"),
        pl.col("2次メッシュコード").n_unique().alias("n_mesh"),
        pl.col("リンク区分").first().alias("kind"),
    ).collect()
    names = pts.select(pl.col("names").list.first()).to_series()
    s["names_per_point_gt1"] = int((pts["names"].list.len() > 1).sum())
    s["mesh_per_point_gt1"] = int((pts["n_mesh"] > 1).sum())
    s["kind"] = pts["kind"].value_counts().sort("kind").to_dicts()
    s["name_empty"] = int(names.fill_null("").str.len_chars().eq(0).sum())
    s["name_route"] = round(float(names.fill_null("").str.contains(ROUTE).mean()), 3)
    s["name_dir"] = round(float(names.fill_null("").str.contains(DIRECTION).mean()), 3)
    s["sample_names"] = names.sample(min(8, len(names)), seed=1).to_list()
    hourly = (
        lf.filter(pl.col("ts").is_not_null())
        .group_by("pid", pl.col("ts").dt.truncate("1h").alias("hour"))
        .agg(pl.col("vol").sum().cast(pl.Int32).alias("vehicles"), pl.col("vol").count().cast(pl.Int16).alias("n_5min"))
        .sort("pid", "hour")
        .collect()
    )
    hp = os.path.join(WORK, f"{region}_hourly.parquet")
    hourly.write_parquet(hp, compression="zstd")
    s["hourly_rows"] = hourly.height
    s["hourly_over12"] = int((hourly["n_5min"] > 12).sum())
    s["hourly_bytes"] = os.path.getsize(hp)
    s["csv_bytes"] = os.path.getsize(csv)
    os.remove(hp)
    os.remove(csv)
    s["header"] = header
    for k in ("tmin", "tmax"):
        s[k] = str(s[k])
    return s


def main():
    os.makedirs(WORK, exist_ok=True)
    with urllib.request.urlopen(BASE + "/opendata.json", timeout=60) as r:
        listing = json.load(r)
    entry = next(e for e in listing if e["type"] == "typeB")
    print("target month", entry["targetMonth"], "released", entry["releaseDay"], "regions", len(entry["targetList"]))
    tot = {"rows": 0, "points": 0, "hourly_rows": 0, "hourly_bytes": 0, "csv_bytes": 0, "zip_bytes": 0}
    for item in entry["targetList"]:
        link = item["link"]
        region = re.search(r"typeB_([a-z]+)_", link).group(1)
        if ONLY and region not in ONLY:
            continue
        t0 = time.time()
        zp = os.path.join(WORK, region + ".zip")
        get(BASE + link, zp)
        zb = os.path.getsize(zp)
        s = measure(region, zp)
        s["zip_bytes"] = zb
        s["region"] = region
        s["sec"] = round(time.time() - t0)
        print("REGION", json.dumps(s, ensure_ascii=False), flush=True)
        for k in tot:
            tot[k] += s[k]
        time.sleep(5)  # be gentle with the source
    print("TOTAL", json.dumps(tot))


if __name__ == "__main__":
    main()
