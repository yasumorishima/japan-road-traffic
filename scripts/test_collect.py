"""Offline tests for collect.py with a fake source and fake releases."""
import os, sys, types
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(__file__))
import fetch, collect

def run(source, releases=None, upload_fails=(), upload_broken=(), max_days=100):
    """source(layer, road, day) -> set of time codes present, or {code: counters}, or 'fail'.
    Returns (exit code, releases, stdout)."""
    releases = releases if releases is not None else {}
    out = []
    def release_assets(tag):
        return set(releases[tag]) if tag in releases else None
    def gh(*args):
        if args[:2] == ("release", "create"):
            releases.setdefault(args[2], set()); return types.SimpleNamespace(returncode=0, stderr="")
        if args[:2] == ("release", "upload"):
            name = os.path.basename(args[3])
            if name in upload_fails:
                releases[args[2]].add(name)  # the asset got there although the call failed
                return types.SimpleNamespace(returncode=1, stderr="already exists")
            if name in upload_broken:
                return types.SimpleNamespace(returncode=1, stderr="HTTP 502")
            releases[args[2]].add(name); return types.SimpleNamespace(returncode=0, stderr="")
        raise AssertionError(args)
    def get(layer, road, t0, t1, bbox):
        got = source(layer, road, t0[:8])
        if got == "fail":
            raise RuntimeError("down")
        if not isinstance(got, dict):
            got = {c: 1 for c in got}
        return [{"properties": {"時間コード": int(c), "常時観測点コード": i}, "geometry": {"coordinates": [[139.0, 35.0]]}}
                for c in sorted(got) if t0 <= c <= t1 for i in range(got[c])]
    collect.release_assets, collect.gh = release_assets, gh
    fetch.get = get
    sys.argv = ["collect.py", "--max-days", str(max_days)]
    import builtins
    real = builtins.print
    builtins.print = lambda *a, **k: out.append(" ".join(map(str, a)))
    try:
        collect.main(); code = 0
    except SystemExit as e:
        code = e.code
    finally:
        builtins.print = real
    return code, releases, "\n".join(out)

def full(layer, road, day):
    step = 5 if "5m" in layer else 60
    return set(fetch.codes(day, step))

now = datetime.now(collect.JST)
last = (now - timedelta(days=1)).date() if now.hour >= 3 else (now - timedelta(days=2)).date()
D = lambda n: (last - timedelta(days=n)).strftime("%Y%m%d")
names = lambda rel: set().union(*rel.values()) if rel else set()

# 1. everything complete: every layer/road/day of both windows is uploaded
code, rel, out = run(full)
assert code == 0, out
assert len(names(rel)) == 3 * 28 + 2 * 2 * 88, len(names(rel))

# 2. a recent day missing one hour is not uploaded (retried later); an old one near expiry is kept
def gap(layer, road, day):
    s = full(layer, road, day)
    if day in (D(0), D(5), D(87)) and "1h_img" in layer:
        s = {c for c in s if c[8:10] not in ("05", "06", "07")}  # 3 of 24 hours: more than the settled gap
    return s
code, rel, out = run(gap)
assert code == 0, out
assert f"cctv_1h_road3_{D(0)}.csv.gz" not in names(rel), "yesterday with a gap must wait"
assert f"cctv_1h_road3_{D(87)}.csv.gz" in names(rel), "partial day near expiry is kept"
assert f"cctv_1h_road3_{D(5)}.csv.gz" not in names(rel), "a large gap away from expiry waits"
assert f"cctv_1h_road3_{D(86)}.csv.gz" in names(rel) and "codes the source does not have" in out

# 3. day already gone from the source: no empty file is uploaded, also when another layer of that day
#    was archived before the source dropped it
def gone(layer, road, day):
    return set() if day == D(87) else full(layer, road, day)
code, rel, out = run(gone)
assert not any(D(87) in n and "_1h_" in n for n in names(rel)), sorted(n for n in names(rel) if D(87) in n)
tag87 = f"raw-{D(87)[:6]}"
code, rel, out = run(gone, releases={tag87: {f"loop_1h_road3_{D(87)}.csv.gz"}})
assert f"cctv_1h_road3_{D(87)}.csv.gz" not in names(rel) and "no rows" in out, out[-500:]

# 4. the combination without counters is never fetched; a settled day with a small source gap is uploaded,
#    yesterday with the same gap waits
seen = []
def small_gap(layer, road, day):
    seen.append((layer, road))
    s = full(layer, road, day)
    return {c for c in s if not c.endswith("1145")} if day in (D(0), D(1)) and layer.endswith("_5m") else s
code, rel, out = run(small_gap)
assert ("t_travospublic_measure_5m_img", 1) not in seen
assert not any(n.startswith("cctv_5m_road1") for n in names(rel))
assert f"loop_5m_road3_{D(1)}.csv.gz" in names(rel) and f"loop_5m_road3_{D(0)}.csv.gz" not in names(rel)
assert "codes the source does not have" in out

# 5. an upload that errors because the asset is already there is not a failure
code, rel, out = run(full, upload_fails={f"loop_1h_road3_{D(3)}.csv.gz"})
assert code == 0 and "already in" in out, out

# 6. a dead source stops the run after a few files instead of retrying every day
calls = []
def dead(layer, road, day):
    calls.append(day); return "fail"
code, rel, out = run(dead)
assert code == 1 and "looks down" in out and len(calls) == collect.MAX_CONSECUTIVE_FAILS, (code, len(calls))

# 7. already archived days are not fetched again; max-days bounds the work
fetched = []
def count(layer, road, day):
    fetched.append(day); return full(layer, road, day)
code, rel, out = run(count, releases=rel_done if (rel_done := run(full)[1]) else {})
assert code == 0 and fetched == [], fetched[:3]
fetched.clear()
code, rel, out = run(count, max_days=2)
# with the cap, the days about to leave the source come first (oldest first), then the newest days
assert set(fetched) == {D(27), D(26), D(87), D(86)}, sorted(set(fetched))
fetched.clear()
code, rel, out = run(count, max_days=5)
assert set(fetched) == {D(27), D(26), D(25), D(0), D(1), D(87), D(86), D(85)}, sorted(set(fetched))

# 8. the 5% boundary on a settled day: 14 of 288 missing is uploaded, 15 waits
def boundary(layer, road, day):
    s = sorted(full(layer, road, day))
    if layer.endswith("measure_5m") and road == 3 and day == D(3):
        return set(s[14:])
    if layer.endswith("measure_5m") and road == 3 and day == D(4):
        return set(s[15:])
    return set(s)
code, rel, out = run(boundary)
assert f"loop_5m_road3_{D(3)}.csv.gz" in names(rel) and f"loop_5m_road3_{D(4)}.csv.gz" not in names(rel)

# 9. a code with far fewer counters than the rest (a cut response) counts as missing
def thin(layer, road, day):
    s = {c: 10 for c in full(layer, road, day)}
    if layer.endswith("measure_1h") and road == 3 and day == D(0):
        s[min(s)] = 3
    return s
code, rel, out = run(thin)
assert f"loop_1h_road3_{D(0)}.csv.gz" not in names(rel) and f"loop_1h_road3_{D(1)}.csv.gz" in names(rel)

# 10. an upload that really fails makes the run fail
code, rel, out = run(full, upload_broken={f"loop_1h_road3_{D(2)}.csv.gz"})
assert code == 1 and f"loop_1h_road3_{D(2)}" in out
print("test_collect: ALL PASS")
