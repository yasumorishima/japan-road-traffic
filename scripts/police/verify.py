"""Independent recomputation of the hourly file for two regions of a month, from the raw zips, with pandas and
plain Python (no iconv, no polars, no code shared with convert.py). Any difference stops the upload.

Rules recomputed: point 0 and non-integer points dropped; only 5-minute marks inside the month; counts outside
0..32767 or not integers become missing; a (source, point, time) with two different counts is dropped; the same
row seen twice counts once; hourly = sum of the values present, n_5min = how many were present.
"""
import io, os, re, shutil, zipfile
import pandas as pd
import pyarrow.parquet as pq

REGION_RE = re.compile(r"typeB_([a-z]+)_\d{4}_\d{2}\.zip$")
TIME_RE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2}) (\d{1,2}):(\d{2})(?::(\d{2}))?$")


def _name(info):
    n = info.filename
    if not info.flag_bits & 0x800:
        try:
            n = n.encode("cp437").decode("cp932")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return os.path.basename(n)


def _is_zip(data_head):
    return data_head[:2] == b"PK"


def keep_two_smallest(paths, chk):
    """Copy the two smallest regional zips (zip format only) into chk; returns their region names."""
    os.makedirs(chk, exist_ok=True)
    cands = []  # (size, region, opener)
    for p in paths:
        m = REGION_RE.search(os.path.basename(p))
        if m:
            with open(p, "rb") as f:
                if _is_zip(f.read(4)):
                    cands.append((os.path.getsize(p), m.group(1), ("file", p)))
            continue
        with zipfile.ZipFile(p) as z:
            for info in z.infolist():
                m = REGION_RE.search(_name(info))
                if m:
                    with z.open(info) as f:
                        if _is_zip(f.read(4)):
                            cands.append((info.file_size, m.group(1), ("member", p, info)))
    cands.sort(key=lambda c: (c[0], c[1]))
    picked = []
    for size, region, src in cands[:2]:
        dst = os.path.join(chk, region + ".zip")
        if src[0] == "file":
            shutil.copyfile(src[1], dst)
        else:
            with zipfile.ZipFile(src[1]) as z, z.open(src[2]) as f, open(dst, "wb") as out:
                shutil.copyfileobj(f, out, 1 << 22)
        picked.append(region)
    if len(picked) < 2:
        raise SystemExit("fewer than two regions to recompute")
    return picked


def raw_rows(zpath):
    rows, seen = [], set()
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".csv"):
                continue
            data = z.read(info)
            if data in seen:  # the same file twice
                continue
            seen.add(data)
            text = data.decode("cp932")
            lines = text.splitlines()
            head = [h.strip() for h in lines[0].split(",")]
            ix = {h: i for i, h in enumerate(head)}
            for line in lines[1:]:
                if not line.strip():
                    continue
                c = line.split(",")
                rows.append((c[ix["情報源コード"]].strip(), c[ix["計測地点番号"]].strip(), c[ix["時刻"]].strip(),
                             c[ix["断面交通量"]].strip()))
    return rows


def recompute(zpath, year, month):
    keep = {}
    for src, pt, t, v in raw_rows(zpath):
        if not re.fullmatch(r"-?\d+", pt) or int(pt) == 0:
            continue
        m = TIME_RE.match(t)
        if not m:
            continue
        Y, M, D, h, mi, s = (int(x) if x else 0 for x in m.groups())
        if (Y, M) != (year, month) or mi % 5 or s:
            continue
        val = int(v) if re.fullmatch(r"\d+", v) and int(v) <= 32767 else None
        keep.setdefault((src, int(pt), D, h, mi), set()).add(val)
    hourly = {}
    for (src, pt, D, h, mi), vals in keep.items():
        if len(vals) != 1:
            continue  # two different counts for the same time
        val = next(iter(vals))
        k = (src, pt, D, h)
        s, n = hourly.get(k, (0, 0))
        hourly[k] = (s + (val or 0), n + (val is not None))
    rows = [(src, pt, pd.Timestamp(year, month, D, h), (s if n else None), n) for (src, pt, D, h), (s, n) in hourly.items()]
    return pd.DataFrame(rows, columns=["source", "point", "hour_jst", "vehicles", "n_5min"])


def check(ym, chk, regions, outdir):
    year, month = int(ym[:4]), int(ym[5:7])
    tag = f"{year:04d}{month:02d}"
    got = pq.read_table(os.path.join(outdir, f"police_hourly_{tag}.parquet")).to_pandas()
    for region in regions:
        want = recompute(os.path.join(chk, region + ".zip"), year, month)
        if want.empty:
            raise SystemExit(f"{ym} {region}: nothing to recompute")
        g = got[got.source.isin(set(want.source))].copy()
        g["hour_jst"] = pd.to_datetime(g.hour_jst).dt.tz_localize(None)
        a = want.sort_values(["source", "point", "hour_jst"]).reset_index(drop=True)
        b = g.sort_values(["source", "point", "hour_jst"]).reset_index(drop=True)[a.columns]
        if len(a) != len(b):
            raise SystemExit(f"{ym} {region}: {len(b)} hourly rows, recomputed {len(a)}")
        for col in ("source", "point", "hour_jst", "n_5min"):
            if not (a[col].astype(str).to_numpy() == b[col].astype(str).to_numpy()).all():
                raise SystemExit(f"{ym} {region}: column {col} differs")
        va, vb = a.vehicles.astype("Float64"), b.vehicles.astype("Float64")
        if not ((va == vb).fillna(False) | (va.isna() & vb.isna())).all():
            raise SystemExit(f"{ym} {region}: vehicles differ")
        print(f"{ym} {region}: {len(a)} hourly rows recomputed independently, all equal", flush=True)
    shutil.rmtree(chk, ignore_errors=True)
