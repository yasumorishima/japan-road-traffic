"""Independent recomputation of a month for a few regions, from the raw archives, in plain Python (no iconv, no
polars, no code shared with convert.py). It checks the 5-minute file, the hourly file and the point list of each
region it recomputes, and that the manifest has exactly the regions the inputs hold. Any difference stops the
upload.

Regions recomputed: the two smallest, one more chosen by the month among the smaller half, and every region that
comes as an lzh archive (lha prints it; nothing is written under a name taken from the archive).

Rules recomputed: point 0 and non-integer points dropped; only 5-minute marks inside the month; counts outside
0..32767 or not integers become missing; a (source, point, time) with two different counts is dropped; the same
row seen twice counts once; a file that is byte for byte another file of the region is read once;
hourly = sum of the values present, n_5min = how many were present.
"""
import gzip, hashlib, io, json, os, re, shutil, subprocess, zipfile
import pandas as pd
import pyarrow.parquet as pq
import pyarrow.compute as pc

REGION_RE = re.compile(r"^typeB_([a-z]+)_(\d{4})_(\d{2})\.zip$")
TIME_RE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2}) (\d{1,2}):(\d{2})(?::(\d{2}))?$")
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
    half = zips[2: max(3, len(zips) // 2)]
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


def texts(path):
    """Yield (file id, text stream) for every CSV of a regional archive; byte-identical files only once."""
    with open(path, "rb") as f:
        kind = _kind(f.read(8))
    if kind == "lzh":
        proc = subprocess.Popen(["lha", "pq", os.path.abspath(path)], stdout=subprocess.PIPE,
                                env={"PATH": "/usr/bin:/bin", "LANG": "C"}, cwd=os.path.dirname(path))
        yield "lzh", io.TextIOWrapper(proc.stdout, encoding="cp932", newline="")
        if proc.wait() != 0:
            raise SystemExit(f"verify: lha failed on {path}")
        return
    seen = set()
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".csv"):
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


def recompute(path, year, month):
    """{(source, point, day, hour, minute): count or None} for the region, and the set of (source, point) with a
    row at a valid time (conflicting ones included)."""
    keep, points = {}, set()
    for _, t in texts(path):
        head = None
        for line in t:
            line = line.rstrip("\r\n")
            if head is None:
                head = {h.strip(): i for i, h in enumerate(line.split(","))}
                ix = [head["情報源コード"], head["計測地点番号"], head["時刻"], head["断面交通量"]]
                continue
            if not line.strip():
                continue
            c = line.split(",")
            src, pt, tm, v = (c[i].strip() for i in ix)
            if not re.fullmatch(r"-?\d+", pt) or int(pt) == 0:
                continue
            m = TIME_RE.match(tm)
            if not m:
                continue
            Y, M, D, h, mi, s = (int(x) if x else 0 for x in m.groups())
            if (Y, M) != (year, month) or mi % 5 or s:
                continue
            val = int(v) if re.fullmatch(r"\d+", v) and int(v) <= 32767 else None
            k = (src, int(pt), D, h, mi)
            points.add((src, int(pt)))
            old = keep.get(k, keep)
            if old is keep:
                keep[k] = val
            elif old is not CONFLICT and old != val:
                keep[k] = CONFLICT
    return {k: v for k, v in keep.items() if v is not CONFLICT}, points


def _table(path, sources, cols):
    t = pq.read_table(path, columns=cols, filters=[("source", "in", sorted(sources))])
    return t.to_pandas()


def check(ym, chk, regions, outdir, all_regions):
    year, month = int(ym[:4]), int(ym[5:7])
    tag = f"{year:04d}{month:02d}"
    man = json.load(open(os.path.join(outdir, f"police_manifest_{tag}.json"), encoding="utf-8"))
    if sorted(man["regions"]) != sorted(all_regions):
        raise SystemExit(f"{ym}: manifest regions {sorted(man['regions'])} but the inputs hold {sorted(all_regions)}")
    pts = pd.read_csv(gzip.open(os.path.join(outdir, f"police_points_{tag}.csv.gz")), dtype=str)
    for region in regions:
        five, points = recompute(os.path.join(chk, region + ".arc"), year, month)
        if not five:
            raise SystemExit(f"{ym} {region}: nothing to recompute")
        mine = pts[pts.region == region]
        got_points = {(s, int(p)) for s, p in zip(mine.source, mine.point)}
        if got_points != points:
            raise SystemExit(f"{ym} {region}: point list differs ({len(got_points)} written, {len(points)} recomputed)")
        sources = {s for s, _ in points}
        # 5-minute values
        g5 = _table(os.path.join(outdir, f"police_5min_{tag}.parquet"), sources, ["source", "point", "time_jst", "vehicles"])
        g5 = g5[[(s, int(p)) in points for s, p in zip(g5.source, g5.point)]]
        t = pd.to_datetime(g5.time_jst)
        got5 = {(s, int(p), d, h, mi): (None if pd.isna(v) else int(v))
                for s, p, d, h, mi, v in zip(g5.source, g5.point, t.dt.day, t.dt.hour, t.dt.minute, g5.vehicles)}
        if len(got5) != len(g5):
            raise SystemExit(f"{ym} {region}: a 5-minute key is written twice")
        if got5.keys() != five.keys():
            raise SystemExit(f"{ym} {region}: 5-minute keys differ ({len(got5)} written, {len(five)} recomputed)")
        bad = [k for k in five if five[k] != got5[k]]
        if bad:
            raise SystemExit(f"{ym} {region}: {len(bad)} 5-minute values differ, e.g. {bad[0]}")
        # hourly
        hourly = {}
        for (s, p, d, h, mi), v in five.items():
            tot, n = hourly.get((s, p, d, h), (0, 0))
            hourly[(s, p, d, h)] = (tot + (v or 0), n + (v is not None))
        gh = _table(os.path.join(outdir, f"police_hourly_{tag}.parquet"), sources,
                    ["source", "point", "hour_jst", "vehicles", "n_5min"])
        gh = gh[[(s, int(p)) in points for s, p in zip(gh.source, gh.point)]]
        t = pd.to_datetime(gh.hour_jst)
        goth = {}
        for s, p, d, h, mn, v, n in zip(gh.source, gh.point, t.dt.day, t.dt.hour, t.dt.minute, gh.vehicles, gh.n_5min):
            if mn:
                raise SystemExit(f"{ym} {region}: an hour does not start on the hour")
            goth[(s, int(p), d, h)] = (None if pd.isna(v) else int(v), int(n))
        if len(goth) != len(gh):
            raise SystemExit(f"{ym} {region}: an hour is written twice")
        want = {k: ((tot if n else None), n) for k, (tot, n) in hourly.items()}
        if goth.keys() != want.keys():
            raise SystemExit(f"{ym} {region}: hourly keys differ ({len(goth)} written, {len(want)} recomputed)")
        bad = [k for k in want if want[k] != goth[k]]
        if bad:
            raise SystemExit(f"{ym} {region}: {len(bad)} hourly values differ, e.g. {bad[0]}")
        print(f"{ym} {region}: {len(five)} 5-minute and {len(want)} hourly rows and {len(points)} points "
              "recomputed independently, all equal", flush=True)
    shutil.rmtree(chk, ignore_errors=True)
