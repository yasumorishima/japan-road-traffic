"""Convert one month of JARTIC "断面交通量情報" (police vehicle detectors on general roads, 5-minute counts)
into four files:

  police_5min_YYYYMM.parquet    source, point, time_jst, vehicles        (every 5-minute value kept)
  police_hourly_YYYYMM.parquet  source, point, hour_jst, vehicles, n_5min (sum over the hour, number of values)
  police_points_YYYYMM.csv.gz   one row per (source, point): region, name, mesh, link fields, rows, first/last time
  police_manifest_YYYYMM.json   what went in (urls, sha256, members) and every count the gates look at

Input is a list of regional archives (typeB_<region>_YYYY_MM.zip, a zip or, once, an lzh under that name) or
monthly archives that contain them. Regions are read one at a time and lazily, so memory stays near the size of
the deduplicated 5-minute table of the largest region. Nothing is uploaded here; police.py does that after the
gates pass.

Source: 「断面交通量情報」（公益財団法人日本道路交通情報センター）https://www.jartic.or.jp/service/opendata/
"""
import gzip, hashlib, json, os, re, shutil, struct, subprocess, sys, zipfile
import numpy as np
import polars as pl
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
ATTRS = ["name", "mesh2", "link_kind", "link_no", "link_dist_10m", "link_version"]
TIME_FORMATS = ["%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S"]

# The regions of the official page (2026). Every monthly archive from 2017-08 on holds exactly these; the seven
# months before hold 38 to 42 of them (12 or 13 missing in 2017-01..04, 9 in 2017-05..07).
REGIONS = frozenset("""aichi akita aomori asahikawa chiba ehime fukui fukuoka fukushima gifu gumma hakodate hiroshima
hyogo ibaraki ishikawa iwate kagawa kagoshima kanagawa kitami kochi kumamoto kushiro kyoto mie miyagi miyazaki nagano
nagasaki nara niigata oita okayama okinawa osaka saga saitama sapporo shiga shimane shizuoka tochigi tokushima tokyo
tottori toyama wakayama yamagata yamaguchi yamanashi""".split())
ALL_REGIONS_FROM = (2017, 8)
# the regions each of the seven earlier archive months lacks (read from the archive's central directories)
_M13 = frozenset("akita ehime fukuoka gifu hiroshima hyogo kagawa okayama okinawa saitama shimane tokushima tottori".split())
_M9 = frozenset("ehime fukuoka gifu hiroshima hyogo kagawa okayama okinawa tokushima".split())
EARLY_MISSING = {(2017, 1): _M13 - {"hiroshima"}, (2017, 2): _M13, (2017, 3): _M13, (2017, 4): _M13,
                 (2017, 5): _M9, (2017, 6): _M9, (2017, 7): _M9}

# gates, per region (shares of the rows with a usable point number): a month that breaks one is not published
MAX_OUTSIDE_SHARE = 0.001      # rows whose time is not a 5-minute mark inside the month
MAX_CONFLICT_SHARE = 0.001     # (source, point, time) seen twice with different counts
MAX_BAD_VALUE_SHARE = 0.01     # counts that are not an integer 0..32767
MAX_BAD_POINT_SHARE = 0.001    # point numbers that are not integers
MIN_POINTS = 5000              # in the whole month

LZH_METHOD = re.compile(rb"-(lh[0-7d]|lz[s45])-$")


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


def lzh_members(path):
    """[(name, method, original size)] read from the headers of an LHA archive (levels 0, 1 and 2). Raises on
    anything else: lha itself accepts garbage silently (it prints nothing and exits 0)."""
    out = []
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        while True:
            pos = f.tell()
            head = f.read(21)
            # the archive ends with a zero byte (a level-2 header size can also start with one: check the method)
            if head == b"" or (head[0] == 0 and not LZH_METHOD.match(head[2:7])):
                if not out:
                    raise ValueError(f"{path}: no member")
                return out
            if len(head) < 21 or not LZH_METHOD.match(head[2:7]):
                raise ValueError(f"{path}: not an LHA header at {pos}")
            method = head[2:7].decode()
            packed, orig = struct.unpack("<II", head[7:15])
            level = head[20]
            name = dirname = b""
            if level in (0, 1):
                want = head[0] + 2 - 21
                rest = f.read(want) if want > 0 else b""
                # name length, name, CRC (and for level 1 the OS byte and the first extended header size)
                if len(rest) != want or len(rest) < 3 or len(rest) < 3 + rest[0] + 3 * (level == 1):
                    raise ValueError(f"{path}: bad level-{level} header at {pos}")
                nlen = rest[0] if rest else 0
                name = rest[1:1 + nlen]
                nxt = struct.unpack("<H", rest[-2:])[0] if level == 1 else 0
                while nxt:
                    ext = f.read(nxt)
                    if len(ext) != nxt:
                        raise ValueError(f"{path}: truncated header at {pos}")
                    packed -= nxt
                    if ext[0] == 1:
                        name = ext[1:-2]
                    elif ext[0] == 2:
                        dirname = ext[1:-2]
                    nxt = struct.unpack("<H", ext[-2:])[0]
            elif level == 2:
                hsize = struct.unpack("<H", head[0:2])[0]
                rest = f.read(hsize - 21)
                if len(rest) != hsize - 21:
                    raise ValueError(f"{path}: truncated header at {pos}")
                nxt, p = struct.unpack("<H", rest[3:5])[0], 5
                while nxt:
                    ext = rest[p:p + nxt]
                    if len(ext) != nxt:
                        raise ValueError(f"{path}: bad extended header at {pos}")
                    if ext[0] == 1:
                        name = ext[1:-2]
                    elif ext[0] == 2:
                        dirname = ext[1:-2]
                    p += nxt
                    nxt = struct.unpack("<H", ext[-2:])[0]
            else:
                raise ValueError(f"{path}: LHA header level {level} at {pos}")
            full = (dirname.replace(b"\xff", b"/") + name) if dirname else name
            out.append((full.decode("cp932", "replace"), method, orig))
            if f.tell() + packed > size:
                raise ValueError(f"{path}: member {out[-1][0]!r} runs past the end")
            f.seek(packed, 1)


def lha(args, cwd, stdout=None):
    # lha runs with an empty environment (no token) and only writes to stdout: nothing it reads can name a file
    return subprocess.run(["lha", *args], cwd=cwd, env={"PATH": "/usr/bin:/bin", "LANG": "C"}, stdout=stdout,
                          stderr=subprocess.PIPE)


def lzh_csvs(path, workdir):
    """Write the CSV members of an LHA archive to workdir/m{i}.csv (names are never used as paths)."""
    members = lzh_members(path)
    files = []
    for name, method, orig in members:
        if method == "-lhd-":
            if "|" in name or orig:
                raise ValueError(f"{path}: link or odd directory entry {name!r}")
            continue
        if not name.lower().endswith(".csv") or "|" in name:
            raise ValueError(f"{path}: unexpected member {name!r}")
        files.append((name, orig))
    r = lha(["tq", os.path.abspath(path)], workdir, stdout=subprocess.DEVNULL)
    if r.returncode != 0:
        raise ValueError(f"{path}: lha test failed: {r.stderr.decode(errors='replace')[:200]}")
    allp = os.path.join(workdir, "lzh.all")
    with open(allp, "wb") as out:
        r = lha(["pq", os.path.abspath(path)], workdir, stdout=out)
    if r.returncode != 0 or os.path.getsize(allp) != sum(o for _, o in files):
        raise ValueError(f"{path}: lha printed {os.path.getsize(allp)} bytes, headers say {sum(o for _, o in files)}")
    items = []
    with open(allp, "rb") as src:  # members come out in archive order, one after another
        for i, (name, orig) in enumerate(files):
            p = os.path.join(workdir, f"m{i}.csv")
            with open(p, "wb") as dst:
                left = orig
                while left:
                    b = src.read(min(left, 1 << 22))
                    if not b:
                        raise ValueError(f"{path}: lha output ends inside {name!r}")
                    dst.write(b)
                    left -= len(b)
            items.append((name, p))
    os.remove(allp)
    return items


def extract_csvs(region_zip, workdir, nested=False):
    """Write every CSV of a regional archive to workdir; returns ([(name, path, sha256)], dropped) with
    byte-identical copies removed (one archive shipped the same file twice under two names). A member that is
    neither a directory nor a CSV stops the month. A regional zip whose only file is the same regional archive
    again (2023-06 Akita: typeB_akita_2023_06/typeB_akita_2023_06.zip) is opened one level down, once."""
    out, seen = [], {}
    os.makedirs(workdir, exist_ok=True)
    if not nested and not is_lzh(region_zip):
        with zipfile.ZipFile(region_zip) as z:
            files = [i for i in z.infolist() if not i.is_dir()]
            wrapped = len(files) == 1 and member_name(files[0]).rsplit("/", 1)[-1] == os.path.basename(region_zip)
            if wrapped:
                inner = os.path.join(workdir, "inner.arc")  # never a name taken from the archive
                with z.open(files[0]) as src, open(inner, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 22)
        if wrapped:
            try:
                return extract_csvs(inner, workdir, nested=True)
            finally:
                os.remove(inner)
    if is_lzh(region_zip):
        items = lzh_csvs(region_zip, workdir)
    else:
        items = []
        with zipfile.ZipFile(region_zip) as z:
            for i, info in enumerate(z.infolist()):
                if info.is_dir():
                    continue
                if not info.filename.lower().endswith(".csv"):
                    raise ValueError(f"{region_zip}: unexpected member {member_name(info)!r}")
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
    if not out:
        raise ValueError(f"{region_zip}: no CSV member")
    return out, dropped


def to_utf8(path):
    dst = path + ".u8"
    with open(path, "rb") as src, open(dst, "wb") as out:
        subprocess.run(["iconv", "-f", "CP932", "-t", "UTF-8"], stdin=src, stdout=out, check=True)
    os.remove(path)
    return dst


def canonical_header(line):
    cols = [c.strip() for c in line.rstrip("\r\n").split(",")]
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


def scan_region(csv_paths):
    """All rows of one region as a lazy frame with canonical columns (strings, plus time_jst)."""
    frames = []
    for p in csv_paths:
        with open(p, encoding="utf-8") as f:
            names = canonical_header(f.readline())
        lf = pl.scan_csv(p, has_header=False, skip_rows=1, new_columns=names, infer_schema=False,
                         truncate_ragged_lines=False, quote_char=None)
        lf = lf.with_columns(pl.col(c).str.strip_chars() for c in names)
        # an empty source code is read as missing; keep it as "" so it joins and matches like any other code
        lf = lf.with_columns(pl.col("source").fill_null(""))
        for k in OPTIONAL:
            if k not in names:
                lf = lf.with_columns(pl.lit(None, pl.String).alias(k))
        frames.append(lf.select(["time", *REQUIRED[1:], *OPTIONAL]))
    return pl.concat(frames).with_columns(parse_time(pl.col("time")).alias("time_jst"))


def collect(lf):
    return lf.collect(engine="streaming")


def convert_region(lf, year, month, stats):
    """Returns (five-minute frame, points frame) for one region and fills stats."""
    start = pl.datetime(year, month, 1, time_unit="ms")
    end = pl.datetime(year + (month == 12), month % 12 + 1, 1, time_unit="ms")
    lf = lf.with_columns(pl.col("point").cast(pl.Int32, strict=False).alias("point_i"),
                         pl.col("vehicles").cast(pl.Int64, strict=False).alias("v"))
    good_point = pl.col("point_i").is_not_null() & (pl.col("point_i") != 0)
    ok_time = ((pl.col("time_jst") >= start) & (pl.col("time_jst") < end) & (pl.col("time_jst").dt.minute() % 5 == 0)
               & (pl.col("time_jst").dt.second() == 0)).fill_null(False)
    ok_v = (pl.col("v") >= 0) & (pl.col("v") <= 32767)
    c = collect(lf.select(
        pl.len().alias("rows_in"),
        pl.col("point_i").is_null().sum().alias("point_not_integer"),
        (pl.col("point_i") == 0).sum().alias("point_zero_rows"),
        (good_point & ~ok_time).sum().alias("outside_rows"),
        (good_point & ok_time & ~ok_v.fill_null(False)).sum().alias("bad_value_rows"))).row(0, named=True)
    stats.update({k: int(v or 0) for k, v in c.items()})
    kept = lf.filter(good_point & ok_time)
    # attributes first, while nothing else is held: the most common non-empty value of each field in the month
    # (ties: the smallest value); one small count per field (one group_by over all eight strings took far more)
    attrs = []
    for a in ATTRS:
        attrs.append(collect(kept.filter(pl.col(a).is_not_null() & (pl.col(a) != ""))
                             .group_by("source", "point_i", a).agg(pl.len().alias("n")))
                     .sort(["source", "point_i", "n", a], descending=[False, False, True, False])
                     .group_by("source", "point_i", maintain_order=True)
                     .agg(pl.col(a).first(), pl.col(a).n_unique().cast(pl.Int32).alias("n_" + a)))
    # one narrow typed table (about 16 bytes a row) holds what the 5-minute values need; the strings stay on disk.
    # The source code becomes an Enum in sorted order, so sorting by it sorts by the code's text.
    codes = pl.Enum(sorted(x for x in collect(kept.select(pl.col("source").unique()))["source"] if x is not None))
    key = ["source", "point_i", "time_jst"]
    # sort, then cut where the key changes: a hash group_by over every row took 8 to 25 times the table's size
    s = collect(kept.select(pl.col("source").cast(codes), "point_i", "time_jst",
                            pl.when(ok_v).then(pl.col("v")).otherwise(None).cast(pl.Int16).alias("v"))
                ).sort([*key, "v"], nulls_last=False)
    first, last = run_bounds(s, key)
    distinct = run_bounds(s, [*key, "v"])[0]  # the start of each distinct (key, value) run
    nv = pl.Series(np.searchsorted(distinct.to_numpy(), last.to_numpy(), side="right")
                   - np.searchsorted(distinct.to_numpy(), first.to_numpy(), side="left"))
    del distinct
    g = s.select(pl.all().gather(first)).with_columns(nv.alias("nv"), (last - first + 1).cast(pl.Int32).alias("k"))
    del s, first, last, nv
    pf, pe = run_bounds(g, ["source", "point_i"])  # g is sorted by point and time
    kc = g["k"].cast(pl.Int64).cum_sum()
    per_point = g.select(pl.col("source", "point_i").gather(pf), pl.col("time_jst").gather(pf).alias("first_jst"),
                         pl.col("time_jst").gather(pe).alias("last_jst")).with_columns(
        (kc.gather(pe) - kc.gather(pf) + g["k"].cast(pl.Int64).gather(pf)).alias("rows"))
    del kc
    d = g.select(single_rows=pl.col("k").filter(pl.col("nv") == 1).sum(), single_keys=(pl.col("nv") == 1).sum(),
                 conflict_keys=(pl.col("nv") > 1).sum(), conflict_rows=pl.col("k").filter(pl.col("nv") > 1).sum()
                 ).row(0, named=True)
    stats.update(same_value_duplicates=int((d["single_rows"] or 0) - d["single_keys"]),
                 conflict_keys=int(d["conflict_keys"]), conflict_rows=int(d["conflict_rows"] or 0))
    # a time with two different counts for one point is dropped, and counted
    # (g follows the sort above, so five comes out sorted by source, point and time)
    five = g.filter(pl.col("nv") == 1).select(pl.col("source"), pl.col("point_i").alias("point"),
                                              pl.col("time_jst"), pl.col("v").alias("vehicles"))
    del g
    stats["rows_out"] = five.height
    stats["points"] = run_bounds(five, ["source", "point"])[0].len()
    five = five.with_columns(pl.col("source").cast(pl.String))
    per_point = per_point.with_columns(pl.col("source").cast(pl.String))
    points = per_point
    for m in attrs:
        points = points.join(m, on=["source", "point_i"], how="left")
    points = (points.with_columns(pl.col("n_name").fill_null(0), pl.col("n_mesh2").fill_null(0))
              .select(pl.col("source"), pl.col("point_i").cast(pl.Int32).alias("point"), "name", "n_name", "mesh2",
                      "n_mesh2", "link_kind", "link_no", "link_dist_10m", "link_version",
                      pl.col("rows").cast(pl.Int64), "first_jst", "last_jst")
              .rename({"n_name": "n_names"})
              .sort("source", "point"))
    stats["points_several_names"] = int((points["n_names"] > 1).sum())
    stats["points_several_meshes"] = int((points["n_mesh2"] > 1).sum())
    return five, points


def run_bounds(df, keys):
    """First and last row (Int64 Series) of each run of equal keys in a frame sorted by those keys."""
    if df.height == 0:
        e = pl.Series([], dtype=pl.Int64)
        return e, e
    new = pl.int_range(pl.len()) == 0
    for k in keys:
        new = new | pl.col(k).ne_missing(pl.col(k).shift(1))
    first = df.select(pl.arg_where(new)).to_series().cast(pl.Int64)
    last = pl.concat([first.slice(1) - 1, pl.Series([df.height - 1], dtype=pl.Int64)])
    return first, last


def hourly_of(five):
    return (five.group_by("source", "point", pl.col("time_jst").dt.truncate("1h").alias("hour_jst"))
            .agg(pl.col("vehicles").sum().cast(pl.Int32).alias("vehicles"),
                 pl.col("vehicles").count().cast(pl.Int8).alias("n_5min"))
            .with_columns(pl.when(pl.col("n_5min") > 0).then(pl.col("vehicles")).otherwise(None).alias("vehicles"))
            .sort("source", "point", "hour_jst"))


def region_pattern(year, month):
    return re.compile(rf"typeB_([a-z]+)_{year:04d}_{month:02d}\.zip$")


def regional_zips(paths, workdir, year, month):
    """Yield (region, path, is_temporary) one region at a time: regional archives pass through, monthly archives
    are expanded member by member (the caller removes a temporary file after use). Any member of a monthly
    archive that is not a regional archive of this month stops the month, as does a region given twice."""
    pat = region_pattern(year, month)
    os.makedirs(workdir, exist_ok=True)
    seen = set()

    def once(region):
        if region in seen:
            raise ValueError(f"region given twice: {region}")
        seen.add(region)

    for p in paths:
        m = pat.match(os.path.basename(p))
        if m:
            once(m.group(1))
            yield m.group(1), p, False
            continue
        with zipfile.ZipFile(p) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                n = member_name(info)
                m = pat.match(n)  # no directory part: the name is the whole member name
                if not m:
                    raise ValueError(f"{os.path.basename(p)}: unexpected member {n!r}")
                once(m.group(1))
                dst = os.path.join(workdir, n)
                with z.open(info) as src, open(dst, "wb") as f:
                    shutil.copyfileobj(src, f, 1 << 22)
                yield m.group(1), dst, True


def convert_month(ym, inputs, outdir, workdir, sources=None, check_gates=True):
    year, month = int(ym[:4]), int(ym[5:7])
    tag = f"{year:04d}{month:02d}"
    os.makedirs(outdir, exist_ok=True)
    manifest = {"month": ym, "inputs": [], "regions": {}, "source_note":
                "「断面交通量情報」（公益財団法人日本道路交通情報センター）（https://www.jartic.or.jp/service/opendata/）を加工して作成"}
    for p in inputs:
        manifest["inputs"].append({"file": os.path.basename(p), "bytes": os.path.getsize(p), "sha256": sha256(p),
                                   "url": (sources or {}).get(p)})
    five_path = os.path.join(outdir, f"police_5min_{tag}.parquet")
    hour_path = os.path.join(outdir, f"police_hourly_{tag}.parquet")
    w5 = wh = None
    all_points = []
    for region, rz, temporary in regional_zips(inputs, workdir, year, month):
        st = {"archive": "lzh" if is_lzh(rz) else "zip"}
        rdir = os.path.join(workdir, "r_" + region)
        members, dropped = extract_csvs(rz, rdir)
        if temporary:
            os.remove(rz)
        st["members"] = [m[0] for m in members]
        st["identical_members_dropped"] = dropped
        five, points = convert_region(scan_region([to_utf8(m[1]) for m in members]), year, month, st)
        hourly = hourly_of(five)
        t5, th = five.to_arrow(), hourly.to_arrow()
        if w5 is None:
            w5 = pq.ParquetWriter(five_path, t5.schema, compression="zstd", compression_level=9)
            wh = pq.ParquetWriter(hour_path, th.schema, compression="zstd", compression_level=9)
        w5.write_table(t5, row_group_size=2_000_000)
        wh.write_table(th, row_group_size=1_000_000)
        st["hourly_rows"] = hourly.height
        del five, hourly, t5, th
        all_points.append(points.with_columns(pl.lit(region).alias("region")))
        manifest["regions"][region] = st
        shutil.rmtree(rdir, ignore_errors=True)
        print("region", region, json.dumps({k: v for k, v in st.items() if k != "members"}, ensure_ascii=False,
                                           default=str)[:400], flush=True)
    if w5 is None:
        raise ValueError("no region found in the inputs")
    w5.close(); wh.close()
    pts = pl.concat(all_points).select("region", pl.exclude("region"))
    pts_path = os.path.join(outdir, f"police_points_{tag}.csv.gz")
    with gzip.open(pts_path, "wb") as f:
        pts.write_csv(f)
    R = manifest["regions"].values()
    tot = {k: sum(r[k] for r in R) for k in ("rows_in", "rows_out", "points", "point_not_integer", "point_zero_rows",
                                              "outside_rows", "bad_value_rows", "conflict_rows",
                                              "same_value_duplicates", "hourly_rows")}
    tot["regions"] = len(manifest["regions"])
    manifest["totals"] = tot
    manifest["regions_missing"] = sorted(REGIONS - set(manifest["regions"]))
    fails = gate_failures(year, month, manifest["regions"], tot)
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


def gate_failures(year, month, regions, tot):
    f = []
    names = set(regions)
    if names - REGIONS:
        f.append(f"unknown regions {sorted(names - REGIONS)}")
    # from 2017-08 every region; before, every region the archive holds for that month
    expected = REGIONS - EARLY_MISSING.get((year, month), frozenset())
    if (year, month) < ALL_REGIONS_FROM and (year, month) not in EARLY_MISSING:
        f.append(f"no region list for {year}-{month:02d}")
    elif expected - names:
        f.append(f"regions missing {sorted(expected - names)}")
    for r in sorted(regions):
        s = regions[r]
        rows = max(s["rows_in"] - s["point_zero_rows"], 1)
        for k, lim in (("point_not_integer", MAX_BAD_POINT_SHARE), ("outside_rows", MAX_OUTSIDE_SHARE),
                       ("conflict_rows", MAX_CONFLICT_SHARE), ("bad_value_rows", MAX_BAD_VALUE_SHARE)):
            if s[k] / rows > lim:
                f.append(f"{r} {k} {s[k]}")
        if s["rows_out"] == 0:
            f.append(f"{r} no rows")
    if tot["points"] < MIN_POINTS:
        f.append(f"points {tot['points']}")
    return f


if __name__ == "__main__":
    ym, outdir, workdir, *inputs = sys.argv[1:]
    convert_month(ym, inputs, outdir, workdir)
