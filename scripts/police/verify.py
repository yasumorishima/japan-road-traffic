"""Independent recomputation of a month for a few regions, from the raw archives, in plain Python (no iconv, no
polars, no code shared with convert.py). It checks the 5-minute file, the hourly file and the point list (with every
attribute) of each region it recomputes, and that the manifest has exactly the regions the inputs hold. Any difference stops the
upload.

Regions recomputed: the two smallest, one more chosen by the month among all but the largest quarter, and every region that
comes as an lzh archive (lha prints it; nothing is written under a name taken from the archive).

Rules recomputed: point 0 and non-integer points dropped; only 5-minute marks inside the month; counts outside
0..32767 or not integers become missing; a (source, point, time) with two different counts is dropped; the same
row seen twice counts once; a file that is byte for byte another file of the region is read once;
hourly = sum of the values present, n_5min = how many were present.
"""
import gzip, hashlib, io, json, os, re, shutil, subprocess, zipfile
import pandas as pd
import pyarrow.parquet as pq
from collections import Counter
from datetime import datetime

REGION_RE = re.compile(r"^typeB_([a-z]+)_(\d{4})_(\d{2})\.zip$")
# (ASCII digits only, one or two for the clock fields, as the converter's parser accepts)
TIME_RE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2}) (\d{1,2}):(\d{1,2})(?::(\d{1,2}))?$", re.ASCII)
INT_RE = re.compile(r"[+-]?\d+", re.ASCII)  # what an integer cast accepts: a sign and ASCII digits
CONFLICT = object()


def _name(info):
    n = info.filename
    if not info.flag_bits & 0x800:
        try:
            n = n.encode("cp437").decode("cp932")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return n


def _kind(head):
    if head[:2] == b"PK":
        return "zip"
    if len(head) >= 7 and head[2:4] == b"-l" and head[6:7] == b"-":
        return "lzh"
    return "?"


def regional_members(paths, year, month):
    """[(region, size, source)] of every regional archive in the inputs, found independently of convert.py."""
    out = []
    for p in paths:
        m = REGION_RE.match(os.path.basename(p))
        if m:
            if (int(m.group(2)), int(m.group(3))) != (year, month):
                raise SystemExit(f"verify: {os.path.basename(p)} is not of {year}-{month:02d}")
            out.append((m.group(1), os.path.getsize(p), ("file", p)))
            continue
        with zipfile.ZipFile(p) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                m = REGION_RE.match(_name(info))
                if not m or (int(m.group(2)), int(m.group(3))) != (year, month):
                    raise SystemExit(f"verify: unexpected member {_name(info)!r} in {os.path.basename(p)}")
                out.append((m.group(1), info.file_size, ("member", p, info)))
    return out


def pick(paths, chk, ym):
    """Copy the regions to recompute into chk; returns ([regions], [every region in the inputs])."""
    year, month = int(ym[:4]), int(ym[5:7])
    os.makedirs(chk, exist_ok=True)
    found = []
    for region, size, src in regional_members(paths, year, month):
        if src[0] == "file":
            with open(src[1], "rb") as f:
                kind = _kind(f.read(8))
        else:
            with zipfile.ZipFile(src[1]) as z, z.open(src[2]) as f:
                kind = _kind(f.read(8))
        found.append((size, region, kind, src))
    found.sort(key=lambda f: (f[0], f[1]))
    zips = [f for f in found if f[2] == "zip"]
    chosen = {f[1] for f in zips[:2]}
    # the third region: chosen by the month among all but the largest quarter (bounded memory for plain Python)
    half = zips[2: max(3, 3 * len(zips) // 4)]
    if half:
        chosen.add(half[int(hashlib.sha256(ym.encode()).hexdigest(), 16) % len(half)][1])
    chosen |= {f[1] for f in found if f[2] == "lzh"}
    if len(chosen) < min(2, len(found)):
        raise SystemExit("verify: fewer than two regions to recompute")
    for size, region, kind, src in found:
        if region not in chosen:
            continue
        dst = os.path.join(chk, region + ".arc")  # copied before convert runs: convert cannot change what is checked
        if src[0] == "file":
            shutil.copyfile(src[1], dst)
        else:
            with zipfile.ZipFile(src[1]) as z, z.open(src[2]) as f, open(dst, "wb") as out:
                shutil.copyfileobj(f, out, 1 << 22)
    return sorted(chosen), sorted(f[1] for f in found)


def texts(path, year=None, month=None, seen=None):
    """Yield (file id, text stream) for every CSV of a regional archive; byte-identical files only once. A zip
    member that is this region's own archive of the month (2023-06 Akita) is read too, one level down."""
    with open(path, "rb") as f:
        kind = _kind(f.read(8))
    if kind == "lzh":
        if seen is not None:
            raise SystemExit(f"verify: an lzh inside {path}")
        proc = subprocess.Popen(["lha", "pq", os.path.abspath(path)], stdout=subprocess.PIPE,
                                env={"PATH": "/usr/bin:/bin", "LANG": "C"}, cwd=os.path.dirname(path))
        yield "lzh", io.TextIOWrapper(proc.stdout, encoding="cp932", newline="")
        if proc.wait() != 0:
            raise SystemExit(f"verify: lha failed on {path}")
        return
    nested, seen = seen is not None, set() if seen is None else seen
    region = os.path.basename(path).split(".", 1)[0]
    with zipfile.ZipFile(path) as z:
        for k, info in enumerate(z.infolist()):
            if info.is_dir():
                continue
            m = REGION_RE.match(_name(info).rsplit("/", 1)[-1])
            if not nested and m and (m.group(1), int(m.group(2)), int(m.group(3))) == (region, year, month):
                inner = f"{path}.{k}.inner"
                with z.open(info) as f, open(inner, "wb") as out:
                    shutil.copyfileobj(f, out, 1 << 22)
                yield from texts(inner, year, month, seen)
                os.remove(inner)
                continue
            if not info.filename.lower().endswith(".csv"):
                continue
            h = hashlib.sha256()
            with z.open(info) as f:
                for b in iter(lambda: f.read(1 << 22), b""):
                    h.update(b)
            if h.digest() in seen:
                continue
            seen.add(h.digest())
            with z.open(info) as f:
                yield info.filename, io.TextIOWrapper(f, encoding="cp932", newline="")


ATTR_COLS = {  # canonical name -> test on the header text
    "name": lambda h: h == "計測地点名称",
    "mesh2": lambda h: h.endswith("次メッシュコード"),
    "link_kind": lambda h: h == "リンク区分",
    "link_no": lambda h: h == "リンク番号",
    "link_dist_10m": lambda h: h.startswith("リンク終端からの距離"),
    "link_version": lambda h: h == "リンクバージョン",
}


def recompute(path, year, month):
    """For one region: {(source, point, day, hour, minute): count or None} (conflicts dropped) and
    {(source, point): {"rows", "first", "last", attribute: Counter}} over every row at a valid time."""
    keep, points = {}, {}
    for _, t in texts(path, year, month):
        head = None
        for line in t:
            line = line.rstrip("\r\n")
            if head is None:
                cols = [h.strip() for h in line.split(",")]
                ix = [cols.index(n) for n in ("情報源コード", "計測地点番号", "時刻", "断面交通量")]
                ax = {a: next((i for i, h in enumerate(cols) if test(h)), None) for a, test in ATTR_COLS.items()}
                head = cols
                continue
            if not line.strip():
                continue
            c = line.split(",")
            # a short row has its missing trailing fields empty, as the converter reads it
            src, pt, tm, v = (c[i].strip() if i < len(c) else "" for i in ix)
            if not INT_RE.fullmatch(pt) or not -2**31 <= int(pt) < 2**31 or int(pt) == 0:
                continue
            m = TIME_RE.match(tm)
            if not m:
                continue
            Y, M, D, h, mi, s = (int(x) if x else 0 for x in m.groups())
            try:
                when = datetime(Y, M, D, h, mi, s)
            except ValueError:  # 24:00, 2020/04/31 and the like are not times
                continue
            if (Y, M) != (year, month) or mi % 5 or s:
                continue
            val = int(v) if INT_RE.fullmatch(v) and 0 <= int(v) <= 32767 else None
            k = (src, int(pt), D, h, mi)
            p = points.get((src, int(pt)))
            if p is None:
                p = points[(src, int(pt))] = {"rows": 0, "first": when, "last": when,
                                             **{a: Counter() for a in ATTR_COLS}}
            p["rows"] += 1
            p["first"], p["last"] = min(p["first"], when), max(p["last"], when)
            for a, i in ax.items():
                x = c[i].strip() if i is not None and i < len(c) else ""
                if x:
                    p[a][x] += 1
            old = keep.get(k, keep)
            if old is keep:
                keep[k] = val
            elif old is not CONFLICT and old != val:
                keep[k] = CONFLICT
    return {k: v for k, v in keep.items() if v is not CONFLICT}, points


def mode(counter):
    """The most common value; ties go to the smallest."""
    if not counter:
        return ""
    top = max(counter.values())
    return min(x for x, n in counter.items() if n == top)


def _table(path, sources, cols):
    return pq.read_table(path, columns=cols, filters=[("source", "in", sorted(sources))]).to_pandas()


def _of_points(df, points):
    return df[pd.MultiIndex.from_arrays([df.source, df.point.astype("int64")]).isin(list(points))]


def check(ym, chk, regions, outdir, all_regions):
    year, month = int(ym[:4]), int(ym[5:7])
    tag = f"{year:04d}{month:02d}"
    man = json.load(open(os.path.join(outdir, f"police_manifest_{tag}.json"), encoding="utf-8"))
    if sorted(man["regions"]) != sorted(all_regions):
        raise SystemExit(f"{ym}: manifest regions {sorted(man['regions'])} but the inputs hold {sorted(all_regions)}")
    pts = pd.read_csv(gzip.open(os.path.join(outdir, f"police_points_{tag}.csv.gz")), dtype=str,
                      keep_default_na=False)
    for region in regions:
        five, points = recompute(os.path.join(chk, region + ".arc"), year, month)
        if not five:
            raise SystemExit(f"{ym} {region}: nothing to recompute")
        # point list and attributes
        mine = pts[pts.region == region]
        got_points = {(s, int(p)) for s, p in zip(mine.source, mine.point)}
        if got_points != set(points) or len(mine) != len(points):
            raise SystemExit(f"{ym} {region}: point list differs ({len(mine)} written, {len(points)} recomputed)")
        bad = []
        for r in mine.itertuples(index=False):
            p = points[(r.source, int(r.point))]
            want = {"rows": str(p["rows"]), "first_jst": p["first"], "last_jst": p["last"],
                    "n_names": str(len(p["name"])), "n_mesh2": str(len(p["mesh2"])),
                    **{a: mode(p[a]) for a in ATTR_COLS}}
            got = {k: getattr(r, k) for k in want}
            got["first_jst"], got["last_jst"] = (pd.Timestamp(got["first_jst"]).to_pydatetime(),
                                                 pd.Timestamp(got["last_jst"]).to_pydatetime())
            if got != want:
                bad.append(((r.source, r.point), {k: (got[k], want[k]) for k in want if got[k] != want[k]}))
        if bad:
            raise SystemExit(f"{ym} {region}: point attributes differ for {len(bad)} points, e.g. {bad[0]}")
        sources = {s for s, _ in points}
        hourly = {}
        for (s, p, d, h, mi), v in five.items():
            tot, n = hourly.get((s, p, d, h), (0, 0))
            hourly[(s, p, d, h)] = (tot + (v or 0), n + (v is not None))
        # 5-minute values: each written row takes its recomputed value out of the dictionary
        g5 = _of_points(_table(os.path.join(outdir, f"police_5min_{tag}.parquet"), sources,
                               ["source", "point", "time_jst", "vehicles"]), points)
        if g5.duplicated(["source", "point", "time_jst"]).any():
            raise SystemExit(f"{ym} {region}: a 5-minute key is written twice")
        t = pd.to_datetime(g5.time_jst)
        n5, nbad, missing = len(five), 0, object()
        for s, p, d, h, mi, v in zip(g5.source, g5.point, t.dt.day, t.dt.hour, t.dt.minute, g5.vehicles):
            w = five.pop((s, int(p), d, h, mi), missing)
            if w is missing:
                raise SystemExit(f"{ym} {region}: 5-minute keys differ ({(s, p, d, h, mi)} written, not recomputed)")
            nbad += w != (None if pd.isna(v) else int(v))
        if five:
            raise SystemExit(f"{ym} {region}: 5-minute keys differ ({len(five)} recomputed, not written)")
        if nbad:
            raise SystemExit(f"{ym} {region}: {nbad} 5-minute values differ")
        del g5, t
        # hourly
        gh = _of_points(_table(os.path.join(outdir, f"police_hourly_{tag}.parquet"), sources,
                               ["source", "point", "hour_jst", "vehicles", "n_5min"]), points)
        if gh.duplicated(["source", "point", "hour_jst"]).any():
            raise SystemExit(f"{ym} {region}: an hour is written twice")
        t = pd.to_datetime(gh.hour_jst)
        if (t.dt.minute != 0).any():
            raise SystemExit(f"{ym} {region}: an hour does not start on the hour")
        nh, nbad = len(hourly), 0
        for s, p, d, h, v, n in zip(gh.source, gh.point, t.dt.day, t.dt.hour, gh.vehicles, gh.n_5min):
            w = hourly.pop((s, int(p), d, h), missing)
            if w is missing:
                raise SystemExit(f"{ym} {region}: hourly keys differ ({(s, p, d, h)} written, not recomputed)")
            tot, cnt = w
            nbad += ((tot if cnt else None), cnt) != ((None if pd.isna(v) else int(v)), int(n))
        if hourly:
            raise SystemExit(f"{ym} {region}: hourly keys differ ({len(hourly)} recomputed, not written)")
        if nbad:
            raise SystemExit(f"{ym} {region}: {nbad} hourly values differ")
        print(f"{ym} {region}: {n5} 5-minute and {nh} hourly rows and {len(points)} points with their attributes "
              "recomputed independently, all equal", flush=True)
    shutil.rmtree(chk, ignore_errors=True)
