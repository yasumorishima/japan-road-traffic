"""Collect JARTIC "断面交通量情報" (police vehicle detectors, 5-minute counts) month by month into the releases
`police-YYYY` (four files per month, see convert.py).

The official page keeps only one month (two months back, replaced on the 1st). Older months come from the archive
that compusophia.com keeps of every official release since 2017 (one zip per month). Months are taken one at a time
with a speed limit, the official month first, then the archive from the newest month back.

A month is uploaded only when convert.py's gates pass and an independent recomputation of two regions agrees.
Months listed in data/police_skip.txt (with a reason) are left out until someone looks at them.

Source: 「断面交通量情報」（公益財団法人日本道路交通情報センター）https://www.jartic.or.jp/service/opendata/
"""
import argparse, json, os, re, shutil, subprocess, sys, time, urllib.request, zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import convert, verify
import collect  # release helpers (gh, release_assets, ensure_release, upload)

OFFICIAL = "https://www.jartic.or.jp/d/opendata"
ARCHIVE = "http://storage.compusophia.com:1475/traffic/typeB/{y:04d}_{m:02d}.zip"
FIRST = (2017, 1)
RATE = os.environ.get("POLICE_RATE", "25M")  # curl --limit-rate: be gentle with both servers
KINDS = ("police_5min_{t}.parquet", "police_hourly_{t}.parquet", "police_points_{t}.csv.gz", "police_manifest_{t}.json")
SKIP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "police_skip.txt")


def ensure_release(tag, cache):
    if tag not in cache:
        names = collect.release_assets(tag)
        if names is None:
            r = collect.gh("release", "create", tag, "--repo", collect.REPO, "--title", tag, "--latest=false", "--notes",
                           f"Police detector counts (断面交通量情報, JARTIC) for {tag[-4:]}: per month a 5-minute and an "
                           "hourly Parquet file, the point table and a manifest. See the README for columns and source.")
            if r.returncode != 0 and collect.release_assets(tag) is None:
                raise RuntimeError(f"create {tag}: {r.stderr.strip()}")
            names = collect.release_assets(tag) or set()
        cache[tag] = names
    return cache[tag]


def official_listing():
    with urllib.request.urlopen(OFFICIAL + "/opendata.json", timeout=60) as r:
        listing = json.load(r)
    e = next(x for x in listing if x["type"] == "typeB")
    m = re.match(r"(\d{4})年(\d{2})月", e["targetMonth"])
    if not m:
        raise RuntimeError(f"unexpected targetMonth {e['targetMonth']!r}")
    return (int(m.group(1)), int(m.group(2))), [x["link"] for x in e["targetList"]]


def months_until(last):
    y, m = FIRST
    while (y, m) <= last:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def names_for(y, m):
    t = f"{y:04d}{m:02d}"
    return [k.format(t=t) for k in KINDS]


def skipped():
    out = {}
    if os.path.exists(SKIP_FILE):
        for line in open(SKIP_FILE, encoding="utf-8"):
            line = line.split("#", 1)[0].strip()
            if line:
                ym, _, why = line.partition(" ")
                out[ym] = why.strip()
    return out


def curl(url, path):
    r = subprocess.run(["curl", "--fail", "--silent", "--show-error", "--location", "--retry", "4", "--retry-delay", "60",
                        "--retry-all-errors", "--limit-rate", RATE, "--max-time", "7200", "-o", path, url])
    if r.returncode != 0:
        raise RuntimeError(f"download failed ({r.returncode}): {url}")
    with zipfile.ZipFile(path) as z:  # a cut-off download has no central directory
        z.infolist()


def fetch_inputs(y, m, official_month, official_links, dl):
    os.makedirs(dl, exist_ok=True)
    sources = {}
    if (y, m) == official_month:
        paths = []
        for link in official_links:
            p = os.path.join(dl, os.path.basename(link))
            curl(OFFICIAL + link, p)
            sources[p] = OFFICIAL + link
            paths.append(p)
            time.sleep(2)
        return paths, sources
    url = ARCHIVE.format(y=y, m=m)
    p = os.path.join(dl, f"{y:04d}_{m:02d}.zip")
    curl(url, p)
    sources[p] = url
    return [p], sources


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-months", type=int, default=1)
    ap.add_argument("--month", help="YYYY-MM: only this month")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--work", default=os.environ.get("WORK", "/tmp/police"))
    a = ap.parse_args()
    official_month, official_links = official_listing()
    print("official month", official_month, "regions", len(official_links))
    skip = skipped()
    cache, todo = {}, []
    for y, m in months_until(official_month):
        ym = f"{y:04d}-{m:02d}"
        if a.month and ym != a.month:
            continue
        if ym in skip:
            print(f"skip {ym}: {skip[ym]}")
            continue
        tag = f"police-{y:04d}"
        if a.dry_run:
            if tag not in cache:
                cache[tag] = collect.release_assets(tag) or set()
            have = cache[tag]
        else:
            have = ensure_release(tag, cache)
        if not set(names_for(y, m)) <= have:
            todo.append((y, m))
    # the official month disappears next month: take it first; then the archive from the newest month back
    todo.sort(key=lambda t: (t != official_month, -(t[0] * 12 + t[1])))
    print(f"{len(todo)} months missing; next: {[f'{y}-{m:02d}' for y, m in todo[:6]]}")
    if a.dry_run:
        return
    for y, m in todo[: a.max_months]:
        ym, tag = f"{y:04d}-{m:02d}", f"police-{y:04d}"
        work = os.path.join(a.work, ym)
        shutil.rmtree(work, ignore_errors=True)
        dl, out, tmp, chk = (os.path.join(work, d) for d in ("dl", "out", "tmp", "chk"))
        t0 = time.time()
        paths, sources = fetch_inputs(y, m, official_month, official_links, dl)
        print(f"{ym}: downloaded {sum(os.path.getsize(p) for p in paths) / 1e9:.2f} GB in {time.time() - t0:.0f} s", flush=True)
        regions = verify.keep_two_smallest(paths, chk)  # raw copies for the independent recomputation
        convert.convert_month(ym, paths, out, tmp, sources=sources)
        verify.check(ym, chk, regions, out)
        for p in paths:
            os.remove(p)
        for name in names_for(y, m):  # the manifest goes last: all four present means the month is complete
            if not collect.upload(tag, os.path.join(out, name), cache):
                raise SystemExit(f"upload of {name} failed")
        print(f"{ym}: uploaded to {tag} in {time.time() - t0:.0f} s", flush=True)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
