"""Daily collector: fetch every complete JST day the source still holds but the releases do not, check it,
and upload it as a release asset (release `raw-YYYYMM`, one gzip CSV per layer / road type / day).

The source keeps 5-minute values for about one month and hourly values for about three months, so a
missed run is recovered by the next one as long as it comes within that window.

A file is uploaded when every time code of the day is present (24 hourly or 288 five-minute codes), or,
from the day before yesterday back, when at most 5% of the codes are missing: the source itself lacks a few
codes on many days and re-querying does not bring them back. A day with a larger gap is retried on later runs and uploaded as it is only when it is about to leave
the source window, and then only if it has rows."""
import argparse, json, os, subprocess, sys, tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import fetch

JST = timezone(timedelta(hours=9))
# (kind, layer, short name, area, step minutes, codes per request, days the source keeps)
LAYERS = [
    ("5m", "t_travospublic_measure_5m", "loop_5m", fetch.KANTO, 5, 12, 28),
    ("5m", "t_travospublic_measure_5m_img", "cctv_5m", fetch.KANTO, 5, 12, 28),
    ("1h", "t_travospublic_measure_1h", "loop_1h", fetch.JAPAN, 60, 4, 88),
    ("1h", "t_travospublic_measure_1h_img", "cctv_1h", fetch.JAPAN, 60, 4, 88),
]
ROADS = (3, 1)
# Combinations with no counters: CCTV counters on expressways inside the Kanto box returned 0 rows on every
# day of 2026-09-05..10-05. Fetching them would only keep those days open forever.
SKIP = {("cctv_5m", 1)}
NEAR_EXPIRY = 3          # days before the end of the source window when an incomplete day is kept as it is
MAX_CONSECUTIVE_FAILS = 3  # stop the run when the source keeps failing instead of burning the job time
REPO = os.environ.get("GITHUB_REPOSITORY", "yasumorishima/japan-road-traffic")

def asset_name(short, road, day):
    return f"{short}_road{road}_{day}.csv.gz"

def gh(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True)

def release_assets(tag):
    """All asset names of the release (paginated), or None when the release does not exist."""
    r = gh("api", f"repos/{REPO}/releases/tags/{tag}", "--jq", ".id")
    if r.returncode != 0:
        if "Not Found" in r.stderr or "HTTP 404" in r.stderr:
            return None
        raise RuntimeError(f"release {tag}: {r.stderr.strip()}")
    rid = r.stdout.strip()
    r = gh("api", "--paginate", f"repos/{REPO}/releases/{rid}/assets?per_page=100", "--jq", ".[].name")
    if r.returncode != 0:
        raise RuntimeError(f"assets of {tag}: {r.stderr.strip()}")
    return set(r.stdout.split())

def ensure_release(tag, cache):
    if tag not in cache:
        names = release_assets(tag)
        if names is None:
            r = gh("release", "create", tag, "--repo", REPO, "--title", tag, "--latest=false",
                   "--notes", f"Daily files for {tag[-6:-2]}-{tag[-2:]} (JST). See the README for columns and source.")
            if r.returncode != 0 and release_assets(tag) is None:
                raise RuntimeError(f"create {tag}: {r.stderr.strip()}")
            names = release_assets(tag) or set()
        cache[tag] = names
    return cache[tag]

def upload(tag, path, cache):
    name = os.path.basename(path)
    r = gh("release", "upload", tag, path, "--repo", REPO)
    if r.returncode == 0:
        cache[tag].add(name); return True
    # an asset of that name may exist already (an earlier run uploaded it): re-read before calling it a failure
    names = release_assets(tag) or set()
    cache[tag] = names
    if name in names:
        print(f"  {name} was already in {tag}"); return True
    print(f"::warning::upload {name}: {r.stderr.strip()[:300]}")
    return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-days", type=int, default=8, help="days per kind per run (bounds runtime)")
    ap.add_argument("--dry-run", action="store_true", help="list what is missing, fetch nothing")
    a = ap.parse_args()
    now = datetime.now(JST)
    # yesterday is complete once hourly values for 23:00 are out (about 85 minutes after the hour)
    last = (now - timedelta(days=1)).date() if now.hour >= 3 else (now - timedelta(days=2)).date()
    cache, failed, gaps, consecutive = {}, [], [], 0
    for kind in ("5m", "1h"):
        layers = [l for l in LAYERS if l[0] == kind]
        keep = layers[0][6]
        days = sorted(last - timedelta(days=i) for i in range(keep))
        todo = []
        for d in days:
            day = d.strftime("%Y%m%d"); tag = f"raw-{day[:6]}"
            have = (release_assets(tag) or set()) if a.dry_run else ensure_release(tag, cache)
            missing = [(l, r) for l in layers for r in ROADS
                       if (l[2], r) not in SKIP and asset_name(l[2], r, day) not in have]
            if missing:
                todo.append((d, day, tag, missing))
        print(f"{kind}: {len(todo)} day(s) missing in {days[0]}..{days[-1]}", flush=True)
        # Days about to leave the source go first (oldest first), then the newest. Oldest-first throughout
        # would let days that stay unsettled hold the cap and keep newer days from being fetched.
        expiring = [t for t in todo if (last - t[0]).days >= keep - NEAR_EXPIRY]
        rest = sorted((t for t in todo if t not in expiring), key=lambda t: t[0], reverse=True)
        for d, day, tag, missing in (expiring + rest)[:a.max_days]:
            if a.dry_run:
                print(f"  would fetch {day}: {[m[0][2] + '_road' + str(m[1]) for m in missing]}"); continue
            near_expiry = (last - d).days >= keep - NEAR_EXPIRY
            with tempfile.TemporaryDirectory() as tmp:
                for (k, layer, short, area, step, chunk, _), road in missing:
                    expected = set(fetch.codes(day, step))
                    rows = []
                    try:
                        cs = sorted(expected)
                        for i in range(0, len(cs), chunk):
                            fetch.fetch_codes(layer, road, cs[i:i + chunk], area, rows)
                        consecutive = 0
                    except Exception as e:
                        consecutive += 1
                        print(f"::warning::{short} road{road} {day}: {e}"); failed.append(f"{short}_road{road}_{day}")
                        if consecutive >= MAX_CONSECUTIVE_FAILS:
                            print(f"::error::{consecutive} files in a row failed; the source looks down, stopping")
                            sys.exit(1)
                        continue
                    per = {}
                    for r in rows:
                        per[str(r[0])] = per.get(str(r[0]), 0) + 1
                    # A code with far fewer counters than the day's median counts as missing (a cut response).
                    med = sorted(per.values())[len(per) // 2] if per else 0
                    present = {c for c, k in per.items() if k >= 0.8 * med}
                    lacking = len(expected - present)
                    # The source itself lacks a few codes on many days (e.g. 287 of 288, measured 2026-09;
                    # re-querying returns nothing), so a day older than yesterday with a small gap is settled.
                    settled = lacking == 0 or ((last - d).days >= 1 and lacking <= max(1, len(expected) // 20))
                    if not settled:
                        if not near_expiry:
                            print(f"  {short} road{road} {day}: {lacking}/{len(expected)} time codes missing, retry later")
                            continue
                        # about to leave the source: keep what is there, but never an empty file
                        if not rows:
                            print(f"  {short} road{road} {day}: not kept (no rows; the source no longer has this day)")
                            continue
                    if lacking:
                        gaps.append(f"{short}_road{road}_{day} ({lacking} of {len(expected)} codes missing)")
                    path = os.path.join(tmp, asset_name(short, road, day))
                    fetch.write_csv(path, rows, layer)
                    if upload(tag, path, cache):
                        print(f"  {short} road{road} {day}: {len(rows)} rows, {len(present)} codes", flush=True)
                    else:
                        failed.append(f"{short}_road{road}_{day}")
    if gaps:
        print(f"uploaded with time codes the source does not have: {len(gaps)} file(s): " + "; ".join(gaps[:20]))
    if failed:
        print(f"::error::{len(failed)} file(s) failed: {failed[:10]}")
        sys.exit(1)

if __name__ == "__main__":
    main()
