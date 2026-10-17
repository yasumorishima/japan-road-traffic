"""Convert one month of JARTIC "断面交通量情報" (police vehicle detectors on general roads, 5-minute counts)
into four files:

  police_5min_YYYYMM.parquet    source, point, time_jst, vehicles        (every 5-minute value kept)
  police_hourly_YYYYMM.parquet  source, point, hour_jst, vehicles, n_5min (sum over the hour, number of values)
  police_points_YYYYMM.csv.gz   one row per (source, point): name, mesh, link fields, rows, first/last time
  police_manifest_YYYYMM.json   what went in (urls, sha256, members) and every count the gates look at

Input is a list of regional zips (typeB_<region>_YYYY_MM.zip) or monthly archives that contain them.
Nothing is uploaded here; police.py does that after the gates pass.

Source: 「断面交通量情報」（公益財団法人日本道路交通情報センター）https://www.jartic.or.jp/service/opendata/
"""
import gzip, hashlib, io, json, os, re, shutil, subprocess, sys, zipfile
from collections import Counter
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

# canonical name -> accepted header spellings (the files changed over the years)
HEADERS = {
    "time": ["時刻"],
    "source": ["情報源コード"],
    "point": ["計測地点番号"],
    "name": ["計測地点名称"],
    "mesh2": ["2次メッシュコード", "２次メッシュコード"],
    "link_kind": ["リンク区分"],
    "link_no": ["リンク番号"],
    "vehicles": ["断面交通量"],
    "link_dist_10m": ["リンク終端からの距離（×10m）", "リンク終端からの距離(10m)", "リンク終端からの距離（10m）", "リンク終端からの距離(×10m)"],
    "link_version": ["リンクバージョン"],
}
REQUIRED = ["time", "source", "point", "name", "mesh2", "link_kind", "vehicles"]
OPTIONAL = ["link_no", "link_dist_10m", "link_version"]
TIME_FORMATS = ["%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S"]

# gates: a month that breaks one of these is not published
MAX_OUTSIDE_SHARE = 0.001   # rows whose time is not a 5-minute mark inside the month
MAX_CONFLICT_SHARE = 0.001  # (source, point, time) seen twice with different counts
MAX_BAD_VALUE_SHARE = 0.01  # counts that are not a non-negative integer
MIN_REGIONS = 30
MIN_POINTS = 5000

FIVE = pl.Schema({"source": pl.String, "point": pl.Int32, "time_jst": pl.Datetime("ms"), "vehicles": pl.Int16})


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def member_name(info):
    n = info.filename
    if not info.flag_bits & 0x800:  # not flagged UTF-8: Japanese names are Shift-JIS read as cp437
        try:
            n = n.encode("cp437").decode("cp932")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return n


def is_lzh(path):
    with open(path, "rb") as f:
        head = f.read(8)
    return len(head) >= 7 and head[2:4] == b"-l" and head[6:7] == b"-"


def extract_csvs(region_zip, workdir):
    """Write every CSV of a regional archive to workdir; returns [(name, path, sha256)] with byte-identical
    copies removed (one archive shipped the same file twice under two names)."""
    out, seen = [], {}
    os.makedirs(workdir, exist_ok=True)
    if is_lzh(region_zip):
        subprocess.run(["lha", "xqw=" + workdir, region_zip], check=True)
        files = [os.path.join(dp, f) for dp, _, fs in os.walk(workdir) for f in fs if f.lower().endswith(".csv")]
        items = [(os.path.relpath(p, workdir), p) for p in sorted(files)]
    else:
        items = []
        with zipfile.ZipFile(region_zip) as z:
            for i, info in enumerate(z.infolist()):
                if info.is_dir() or not info.filename.lower().endswith(".csv"):
                    continue
                p = os.path.join(workdir, f"m{i}.csv")
                with z.open(info) as src, open(p, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 22)
                items.append((member_name(info), p))
    dropped = []
    for name, p in items:
        h = sha256(p)
        if h in seen:
            dropped.append([name, seen[h]])
            os.remove(p)
            continue
        seen[h] = name
        out.append((name, p, h))
    return out, dropped


def to_utf8(path):
    dst = path + ".u8"
    with open(path, "rb") as src, open(dst, "wb") as out:
        subprocess.run(["iconv", "-f", "CP932", "-t", "UTF-8"], stdin=src, stdout=out, check=True)
    os.remove(path)
    return dst


def canonical_header(line):
    cols = [c.strip().lstrip("﻿") for c in line.rstrip("\r\n").split(",")]
    names = []
    for c in cols:
        hit = [k for k, v in HEADERS.items() if c in v]
        if len(hit) != 1:
            raise ValueError(f"unknown column {c!r} in header {line!r}")
        names.append(hit[0])
    if len(set(names)) != len(names):
        raise ValueError(f"repeated column in header {line!r}")
    missing = [k for k in REQUIRED if k not in names]
    if missing:
        raise ValueError(f"header lacks {missing}: {line!r}")
    return names


def parse_time(col):
    e = None
    for f in TIME_FORMATS:
        t = col.str.strptime(pl.Datetime("ms"), f, strict=False)
        e = t if e is None else e.fill_null(t)
    return e


def read_region(csv_paths):
    """All rows of one region as a polars frame with canonical columns (strings except time)."""
    frames = []
    for p in csv_paths:
        with open(p, encoding="utf-8") as f:
            names = canonical_header(f.readline())
        lf = pl.scan_csv(p, has_header=False, skip_rows=1, new_columns=names, infer_schema=False,
                         truncate_ragged_lines=False, quote_char=None)
        lf = lf.with_columns(pl.col(c).str.strip_chars() for c in names)
        for k in OPTIONAL:
            if k not in names:
                lf = lf.with_columns(pl.lit(None, pl.String).alias(k))
        frames.append(lf.select(["time", *REQUIRED[1:], *OPTIONAL]))
    df = pl.concat(frames).collect()
    return df.with_columns(parse_time(pl.col("time")).alias("time_jst"))


def mode_nonempty(s):
    c = Counter(x for x in s if x)
    return c.most_common(1)[0][0] if c else None


def convert_region(df, year, month, stats):
    """Returns (five-minute frame, points frame) for one region and fills stats."""
    n = df.height
    stats["rows_in"] = n
    start = pl.datetime(year, month, 1, time_unit="ms")
    end = pl.datetime(year + (month == 12), month % 12 + 1, 1, time_unit="ms")
    point = pl.col("point").cast(pl.Int32, strict=False)
    df = df.with_columns(point.alias("point_i"))
    bad_point = df["point_i"].null_count()
    zero = int((df["point_i"] == 0).sum())
    df = df.filter(pl.col("point_i").is_not_null() & (pl.col("point_i") != 0))
    ok_time = (pl.col("time_jst") >= start) & (pl.col("time_jst") < end) & (pl.col("time_jst").dt.minute() % 5 == 0) \
        & (pl.col("time_jst").dt.second() == 0)
    outside = int(df.select((~ok_time.fill_null(False)).sum()).item())
    df = df.filter(ok_time.fill_null(False))
    v = pl.col("vehicles").cast(pl.Int64, strict=False)
    df = df.with_columns(v.alias("v"))
    bad_value = int(df.select((pl.col("v").is_null() | (pl.col("v") < 0) | (pl.col("v") > 32767)).sum()).item())
    df = df.with_columns(pl.when((pl.col("v") >= 0) & (pl.col("v") <= 32767)).then(pl.col("v")).otherwise(None)
                         .cast(pl.Int16).alias("v"))
    key = ["source", "point_i", "time_jst"]
    g = df.group_by(key).agg(pl.col("v").n_unique().alias("nv"), pl.len().alias("k"), pl.col("v").first().alias("v"))
    conflicts = g.filter(pl.col("nv") > 1)
    stats.update(point_not_integer=bad_point, point_zero_rows=zero, outside_rows=outside, bad_value_rows=bad_value,
                 same_value_duplicates=int(g.filter(pl.col("nv") == 1)["k"].sum() - g.filter(pl.col("nv") == 1).height),
                 conflict_keys=conflicts.height, conflict_rows=int(conflicts["k"].sum()))
    five = (g.filter(pl.col("nv") == 1)  # a time with two different counts for one point is dropped, and counted
            .select(pl.col("source"), pl.col("point_i").alias("point"), pl.col("time_jst"), pl.col("v").alias("vehicles"))
            .sort("source", "point", "time_jst"))
    stats["rows_out"] = five.height
    stats["points"] = five.select(pl.struct("source", "point").n_unique()).item() if five.height else 0
    pts = (df.group_by("source", "point_i").agg(
        pl.col("name").alias("names"), pl.col("mesh2").alias("meshes"), pl.col("link_kind").alias("kinds"),
        pl.col("link_no").alias("links"), pl.col("link_dist_10m").alias("dists"), pl.col("link_version").alias("versions"),
        pl.len().alias("rows"), pl.col("time_jst").min().alias("first_jst"), pl.col("time_jst").max().alias("last_jst"))
        .sort("source", "point_i"))
    rows = []
    for r in pts.iter_rows(named=True):
        names = Counter(x for x in r["names"] if x)
        rows.append({
            "source": r["source"], "point": r["point_i"],
            "name": names.most_common(1)[0][0] if names else None, "n_names": len(names),
            "mesh2": mode_nonempty(r["meshes"]), "n_mesh2": len({x for x in r["meshes"] if x}),
            "link_kind": mode_nonempty(r["kinds"]), "link_no": mode_nonempty(r["links"]),
            "link_dist_10m": mode_nonempty(r["dists"]), "link_version": mode_nonempty(r["versions"]),
            "rows": r["rows"], "first_jst": r["first_jst"], "last_jst": r["last_jst"],
        })
    points = pl.DataFrame(rows, schema={"source": pl.String, "point": pl.Int32, "name": pl.String, "n_names": pl.Int32,
                                        "mesh2": pl.String, "n_mesh2": pl.Int32, "link_kind": pl.String,
                                        "link_no": pl.String, "link_dist_10m": pl.String, "link_version": pl.String,
                                        "rows": pl.Int64, "first_jst": pl.Datetime("ms"), "last_jst": pl.Datetime("ms")})
    stats["points_several_names"] = int((points["n_names"] > 1).sum())
    stats["points_several_meshes"] = int((points["n_mesh2"] > 1).sum())
    return five, points


def hourly_of(five):
    return (five.group_by("source", "point", pl.col("time_jst").dt.truncate("1h").alias("hour_jst"))
            .agg(pl.col("vehicles").sum().cast(pl.Int32).alias("vehicles"),
                 pl.col("vehicles").count().cast(pl.Int8).alias("n_5min"))
            .with_columns(pl.when(pl.col("n_5min") > 0).then(pl.col("vehicles")).otherwise(None).alias("vehicles"))
            .sort("source", "point", "hour_jst"))


def regional_zips(paths, workdir):
    """Expand monthly archives into regional zips; regional zips pass through. Returns [(region, path)]."""
    out = []
    os.makedirs(workdir, exist_ok=True)
    for p in paths:
        base = os.path.basename(p)
        m = re.match(r"typeB_([a-z]+)_\d{4}_\d{2}\.zip$", base)
        if m:
            out.append((m.group(1), p))
            continue
        with zipfile.ZipFile(p) as z:
            for info in z.infolist():
                n = os.path.basename(member_name(info))
                m = re.match(r"typeB_([a-z]+)_\d{4}_\d{2}\.zip$", n)
                if not m:
                    continue
                dst = os.path.join(workdir, n)
                with z.open(info) as src, open(dst, "wb") as f:
                    shutil.copyfileobj(src, f, 1 << 22)
                out.append((m.group(1), dst))
    regions = [r for r, _ in out]
    dup = [r for r, c in Counter(regions).items() if c > 1]
    if dup:
        raise ValueError(f"region given twice: {dup}")
    return sorted(out)


def convert_month(ym, inputs, outdir, workdir, sources=None, check_gates=True):
    year, month = int(ym[:4]), int(ym[5:7])
    tag = f"{year:04d}{month:02d}"
    os.makedirs(outdir, exist_ok=True)
    manifest = {"month": ym, "inputs": [], "regions": {}, "source_note":
                "「断面交通量情報」（公益財団法人日本道路交通情報センター）を加工して作成"}
    for i, p in enumerate(inputs):
        manifest["inputs"].append({"file": os.path.basename(p), "bytes": os.path.getsize(p), "sha256": sha256(p),
                                   "url": (sources or {}).get(p)})
    five_path = os.path.join(outdir, f"police_5min_{tag}.parquet")
    hour_path = os.path.join(outdir, f"police_hourly_{tag}.parquet")
    w5 = wh = None
    all_points = []
    for region, rz in regional_zips(inputs, workdir):
        st = {}
        rdir = os.path.join(workdir, "r_" + region)
        members, dropped = extract_csvs(rz, rdir)
        st["members"] = [m[0] for m in members]
        st["identical_members_dropped"] = dropped
        df = read_region([to_utf8(m[1]) for m in members])
        five, points = convert_region(df, year, month, st)
        del df
        hourly = hourly_of(five)
        t5, th = five.to_arrow(), hourly.to_arrow()
        if w5 is None:
            w5 = pq.ParquetWriter(five_path, t5.schema, compression="zstd", compression_level=9)
            wh = pq.ParquetWriter(hour_path, th.schema, compression="zstd", compression_level=9)
        w5.write_table(t5, row_group_size=2_000_000)
        wh.write_table(th, row_group_size=1_000_000)
        st["hourly_rows"] = hourly.height
        all_points.append(points.with_columns(pl.lit(region).alias("region")))
        manifest["regions"][region] = st
        shutil.rmtree(rdir, ignore_errors=True)
        if rz.startswith(workdir):
            os.remove(rz)
        print("region", region, json.dumps(st, ensure_ascii=False, default=str)[:400], flush=True)
    if w5 is None:
        raise ValueError("no region found in the inputs")
    w5.close(); wh.close()
    pts = pl.concat(all_points).select("region", pl.exclude("region"))
    pts_path = os.path.join(outdir, f"police_points_{tag}.csv.gz")
    with gzip.open(pts_path, "wb") as f:
        pts.write_csv(f)
    R = manifest["regions"].values()
    tot = {k: sum(r[k] for r in R) for k in ("rows_in", "rows_out", "points", "point_zero_rows", "outside_rows",
                                              "bad_value_rows", "conflict_rows", "same_value_duplicates", "hourly_rows")}
    tot["regions"] = len(manifest["regions"])
    manifest["totals"] = tot
    fails = gate_failures(tot)
    manifest["gate_failures"] = fails
    for p in (five_path, hour_path, pts_path):
        manifest.setdefault("outputs", {})[os.path.basename(p)] = {"bytes": os.path.getsize(p), "sha256": sha256(p)}
    man_path = os.path.join(outdir, f"police_manifest_{tag}.json")
    with open(man_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1, default=str)
    print("totals", json.dumps(tot), "gate failures", fails, flush=True)
    if check_gates and fails:
        raise SystemExit(f"{ym}: gates failed: {fails}")
    return manifest


def gate_failures(tot):
    f = []
    rows = max(tot["rows_in"] - tot["point_zero_rows"], 1)
    if tot["outside_rows"] / rows > MAX_OUTSIDE_SHARE:
        f.append(f"outside rows {tot['outside_rows']}")
    if tot["conflict_rows"] / rows > MAX_CONFLICT_SHARE:
        f.append(f"conflicting rows {tot['conflict_rows']}")
    if tot["bad_value_rows"] / rows > MAX_BAD_VALUE_SHARE:
        f.append(f"bad values {tot['bad_value_rows']}")
    if tot["regions"] < MIN_REGIONS:
        f.append(f"regions {tot['regions']}")
    if tot["points"] < MIN_POINTS:
        f.append(f"points {tot['points']}")
    return f


if __name__ == "__main__":
    ym, outdir, workdir, *inputs = sys.argv[1:]
    convert_month(ym, inputs, outdir, workdir)
