"""Collect JARTIC "断面交通量情報" (police vehicle detectors, 5-minute counts) month by month into the releases
`police-YYYY` (four files per month, see convert.py).

The official page keeps only one month (two months back, replaced on the 1st). Older months come from the archive
that compusophia.com keeps of every official release since 2017 (one zip per month). Months are taken one at a time
with a speed limit, the official month first, then the archive from the newest month back.

A month is complete when its four files are in the release, fully uploaded. It is uploaded only when convert.py's
gates pass and an independent recomputation of a few regions agrees. When converting or checking a month fails,
the run uploads police_failed_YYYYMM.json with the reason and stops (red); later runs leave that month out, so a
broken month is downloaded once and not every six hours. Each run also uploads police_attempt_YYYYMM_N.json once
every file of a month answers a HEAD request (a server that is down, or a month not posted yet, costs nothing), and
the marks go when the month is done: a month started three times without finishing (a cut download, a killed or
timed-out run) is left out the same way. To retry a month left out, delete its police_failed and police_attempt
assets, or run it by name (--month takes a month whatever happened before). Months in data/police_skip.txt (with a
reason) are left out as well. Months in data/police_retry.txt (with a reason) are tried again although they failed
before (the attempt limit still holds), for a fix that only a push can deliver; remove a line once its month is in.

Source: 「断面交通量情報」（公益財団法人日本道路交通情報センター）https://www.jartic.or.jp/service/opendata/
"""
import argparse, http.client, json, os, re, shutil, subprocess, sys, time, urllib.request, zipfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import convert, verify
import collect  # gh, REPO

OFFICIAL = "https://www.jartic.or.jp/d/opendata"
ARCHIVE = "http://storage.compusophia.com:1475/traffic/typeB/{y:04d}_{m:02d}.zip"
FIRST = (2017, 1)
RATE = os.environ.get("POLICE_RATE", "10M")  # curl --limit-rate in bytes/s: be gentle with both servers
KINDS = ("police_5min_{t}.parquet", "police_hourly_{t}.parquet", "police_points_{t}.csv.gz", "police_manifest_{t}.json")
FAILED = "police_failed_{t}.json"
ATTEMPT_PREFIX = "police_attempt_{t}_"  # + n.json
MAX_ATTEMPTS = 3  # runs that started a month and never finished it (download cut, killed, timed out)
SKIP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "police_skip.txt")
RETRY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "police_retry.txt")
JST = timezone(timedelta(hours=9))


def assets(tag):
    """{name: {"id", "state", "size"}} of the release, or None when it does not exist."""
    r = collect.gh("api", f"repos/{collect.REPO}/releases/tags/{tag}", "--jq", ".id")
    if r.returncode != 0:
        if "Not Found" in r.stderr or "HTTP 404" in r.stderr:
            return None
        raise RuntimeError(f"release {tag}: {r.stderr.strip()}")
    rid = r.stdout.strip()
    r = collect.gh("api", "--paginate", f"repos/{collect.REPO}/releases/{rid}/assets?per_page=100",
                   "--jq", '.[] | [.name, .id, .state, .size] | @tsv')
    if r.returncode != 0:
        raise RuntimeError(f"assets of {tag}: {r.stderr.strip()}")
    out = {}
    for line in r.stdout.splitlines():
        name, aid, state, size = line.split("\t")
        out[name] = {"id": aid, "state": state, "size": int(size)}
    return out


def ensure_release(tag):
    have = assets(tag)
    if have is None:
        r = collect.gh("release", "create", tag, "--repo", collect.REPO, "--title", tag, "--latest=false", "--notes",
                       f"Police detector counts (断面交通量情報, JARTIC) for {tag[-4:]}: per month a 5-minute and an "
                       "hourly Parquet file, the point table and a manifest. See the README for columns and source.")
        have = assets(tag)
        if have is None:
            raise RuntimeError(f"create {tag}: {r.stderr.strip()}")
    return have


def delete_asset(name, info):
    r = collect.gh("api", "-X", "DELETE", f"repos/{collect.REPO}/releases/assets/{info['id']}")
    if r.returncode != 0:
        raise RuntimeError(f"delete {name}: {r.stderr.strip()}")


def upload(tag, path):
    r = collect.gh("release", "upload", tag, path, "--repo", collect.REPO, "--clobber")
    if r.returncode != 0:
        raise RuntimeError(f"upload {os.path.basename(path)}: {r.stderr.strip()[:300]}")


def official_listing():
    """(month, links) of the official page. ListingUnavailable when it cannot be read (the archive can still be
    worked on); any other error (an unexpected format, changed regions) stops the run."""
    try:
        with urllib.request.urlopen(OFFICIAL + "/opendata.json", timeout=60) as r:
            listing = json.load(r)
    except (OSError, ValueError, http.client.HTTPException) as e:  # (a cut response is an HTTPException)
        raise ListingUnavailable(str(e)) from e
    e = next(x for x in listing if x["type"] == "typeB")
    m = re.match(r"(\d{4})年(\d{2})月", e["targetMonth"])
    if not m:
        raise RuntimeError(f"unexpected targetMonth {e['targetMonth']!r}")
    ym = (int(m.group(1)), int(m.group(2)))
    links = [x["link"] for x in e["targetList"]]
    pat = convert.region_pattern(*ym)
    regions = {pat.match(os.path.basename(l)).group(1) if pat.match(os.path.basename(l)) else l for l in links}
    if regions != convert.REGIONS or len(links) != len(regions):
        raise RuntimeError(f"the official regions changed: {sorted(regions ^ convert.REGIONS)}; update convert.REGIONS")
    return ym, links


def months_until(last):
    y, m = FIRST
    while (y, m) <= last:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def names_for(y, m):
    t = f"{y:04d}{m:02d}"
    return [k.format(t=t) for k in KINDS]


def complete(have, y, m):
    return all(have.get(n, {}).get("state") == "uploaded" for n in names_for(y, m))


def failed(have, y, m):
    return have.get(FAILED.format(t=f"{y:04d}{m:02d}"), {}).get("state") == "uploaded"


def skipped():
    return month_list(SKIP_FILE)


def retried():
    return month_list(RETRY_FILE)


def month_list(path):
    """{"YYYY-MM": reason} from a file of 'YYYY-MM reason' lines (# starts a comment)."""
    out = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            ym, _, why = line.partition(" ")
            if not re.fullmatch(r"\d{4}-\d{2}", ym) or not why.strip():
                raise SystemExit(f"{path}: expected 'YYYY-MM reason', got {line!r}")
            out[ym] = why.strip()
    return out


def available(url):
    head = subprocess.run(["curl", "--fail", "--silent", "--show-error", "--location", "--head", "--max-time", "60",
                           "--retry", "3", "--retry-delay", "30", url], capture_output=True, text=True)
    if head.returncode != 0:
        raise RuntimeError(f"not available ({head.returncode}): {url} {head.stderr.strip()[:200]}")


def curl(url, path):
    # -C - continues a cut transfer on each retry instead of starting the file again
    r = subprocess.run(["curl", "--fail", "--silent", "--show-error", "--location", "--retry", "4", "--retry-delay", "60",
                        "--retry-all-errors", "-C", "-", "--limit-rate", RATE, "--max-time", "10800", "-o", path, url])
    if r.returncode != 0:
        raise RuntimeError(f"download failed ({r.returncode}): {url}")
    with zipfile.ZipFile(path) as z:  # a cut-off download has no central directory
        z.infolist()


def fetch_inputs(y, m, official_month, official_links, dl, started=lambda: None):
    """Download the month's files. `started` runs once every file has answered a HEAD request, before the first
    byte is fetched: a server that is down or a month not posted yet is not an attempt at the month."""
    os.makedirs(dl, exist_ok=True)
    if (y, m) == official_month:
        urls = [(OFFICIAL + link, os.path.join(dl, os.path.basename(link))) for link in official_links]
    else:
        urls = [(ARCHIVE.format(y=y, m=m), os.path.join(dl, f"{y:04d}_{m:02d}.zip"))]
    for url, _ in urls:
        available(url)
    started()
    sources = {}
    for url, p in urls:
        curl(url, p)
        sources[p] = url
        if len(urls) > 1:
            time.sleep(2)
    return [p for _, p in urls], sources


def convert_and_check(ym, paths, sources, work):
    out, tmp, chk = (os.path.join(work, d) for d in ("out", "tmp", "chk"))
    regions, all_regions = verify.pick(paths, chk, ym)  # raw copies for the independent recomputation
    convert.convert_month(ym, paths, out, tmp, sources=sources)
    for p in paths:
        os.remove(p)
    verify.check(ym, chk, regions, out, all_regions)
    return out


def _note(tag, name, work, **fields):
    os.makedirs(work, exist_ok=True)
    p = os.path.join(work, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({**fields, "commit": os.environ.get("GITHUB_SHA"), "run": os.environ.get("GITHUB_RUN_ID"),
                   "at": datetime.now(JST).isoformat(timespec="seconds")}, f, ensure_ascii=False, indent=1)
    upload(tag, p)


def mark_failed(tag, y, m, err, work):
    _note(tag, FAILED.format(t=f"{y:04d}{m:02d}"), work, month=f"{y:04d}-{m:02d}", error=err[-2000:],
          retry="delete this asset to try the month again")


def attempts(have, y, m):
    """Names of the attempt marks of a month: one is uploaded before each download and all go once it is done."""
    pre = ATTEMPT_PREFIX.format(t=f"{y:04d}{m:02d}")
    return sorted(n for n, a in have.items() if n.startswith(pre) and a.get("state") == "uploaded")


def fallback_last(now):
    """The month two before now's month: the official page's month, when its listing cannot be read."""
    return (now.year, now.month - 2) if now.month > 2 else (now.year - 1, now.month + 10)


class ListingUnavailable(Exception):
    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-months", type=int, default=1)
    ap.add_argument("--month", help="YYYY-MM: only this month")
    ap.add_argument("--dry-run", action="store_true", help="only list the missing months")
    ap.add_argument("--no-upload", action="store_true", help="download, convert and check, but upload nothing")
    ap.add_argument("--work", default=os.environ.get("WORK", "/tmp/police"))
    a = ap.parse_args()
    try:
        official_month, official_links = official_listing()
        last = official_month
        print("official month", official_month, "regions", len(official_links))
    except ListingUnavailable as e:  # the archive can still be worked on
        print(f"::warning::official listing: {e}")
        official_month, official_links = None, []
        last = fallback_last(datetime.now(JST))
    skip, retry = skipped(), retried()
    cache, todo = {}, []
    for y, m in months_until(last):
        ym = f"{y:04d}-{m:02d}"
        if a.month and ym != a.month:
            continue
        if ym in skip:
            print(f"skip {ym}: {skip[ym]}")
            continue
        tag = f"police-{y:04d}"
        if tag not in cache:
            cache[tag] = assets(tag) or {}  # a release is created only when a month of it is worked on
        have = cache[tag]
        if complete(have, y, m):
            continue
        if not a.month:  # asked for by name, a month is tried whatever happened before
            if failed(have, y, m) and ym in retry:
                print(f"{ym} failed before; tried again ({os.path.basename(RETRY_FILE)}: {retry[ym]})")
            elif failed(have, y, m):
                print(f"::warning::{ym} failed before (asset {FAILED.format(t=f'{y:04d}{m:02d}')}); left out")
                continue
            if len(attempts(have, y, m)) >= MAX_ATTEMPTS:
                print(f"::warning::{ym} was started {len(attempts(have, y, m))} times and never finished; left out "
                      f"(delete its {ATTEMPT_PREFIX.format(t=f'{y:04d}{m:02d}')}*.json assets to try again)")
                continue
        todo.append((y, m))
    # newest first: the official month (the newest, and gone from its page next month), then the archive back
    todo.sort(key=lambda t: -(t[0] * 12 + t[1]))
    print(f"{len(todo)} months missing; next: {[f'{y}-{m:02d}' for y, m in todo[:6]]}")
    if a.dry_run:
        return
    for y, m in todo[: a.max_months]:
        ym, tag = f"{y:04d}-{m:02d}", f"police-{y:04d}"
        work = os.path.join(a.work, ym)
        shutil.rmtree(work, ignore_errors=True)
        dl = os.path.join(work, "dl")
        t0 = time.time()
        def started():  # marks the start: a run that dies before the end still counts as an attempt
            if not a.no_upload:
                n = len(attempts(ensure_release(tag), y, m)) + 1
                _note(tag, ATTEMPT_PREFIX.format(t=f"{y:04d}{m:02d}") + f"{n}.json", a.work, month=ym, attempt=n)
        paths, sources = fetch_inputs(y, m, official_month, official_links, dl, started)
        print(f"{ym}: downloaded {sum(os.path.getsize(p) for p in paths) / 1e9:.2f} GB in {time.time() - t0:.0f} s", flush=True)
        try:
            out = convert_and_check(ym, paths, sources, work)
        except BaseException as e:
            if isinstance(e, KeyboardInterrupt) or a.no_upload:
                raise
            mark_failed(tag, y, m, f"{type(e).__name__}: {e}", a.work)
            raise
        if a.no_upload:
            print(f"{ym}: converted and checked in {time.time() - t0:.0f} s; nothing uploaded (--no-upload)", flush=True)
            shutil.rmtree(work, ignore_errors=True)
            continue
        have = assets(tag) or {}
        for name in names_for(y, m):  # an earlier run's partial files
            if name in have:
                delete_asset(name, have[name])
        # the three data files, checked, then the manifest: a month is complete only once its manifest is there
        *data, manifest = names_for(y, m)
        for group in (data, [manifest]):
            for name in group:
                upload(tag, os.path.join(out, name))
            have = assets(tag) or {}
            for name in group:
                want = os.path.getsize(os.path.join(out, name))
                got = have.get(name, {})
                if got.get("state") != "uploaded" or got.get("size") != want:
                    raise SystemExit(f"{ym}: {name} in {tag} is {got} after upload, expected {want} bytes")
        for name in [FAILED.format(t=f"{y:04d}{m:02d}"), *attempts(have, y, m)]:  # the month is done
            if name in have:
                delete_asset(name, have[name])
        print(f"{ym}: uploaded to {tag} and checked in {time.time() - t0:.0f} s", flush=True)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
