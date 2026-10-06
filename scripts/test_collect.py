"""Offline tests for collect.py with a fake source and fake releases."""
import os, sys, types
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(__file__))
import fetch, collect

def run(source, releases=None, upload_fails=(), max_days=100):
    """source(layer, road, day) -> set of time codes present (or 'fail'). Returns (exit code, releases, stdout)."""
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
            releases[args[2]].add(name); return types.SimpleNamespace(returncode=0, stderr="")
        raise AssertionError(args)
    def get(layer, road, t0, t1, bbox):
        got = source(layer, road, t0[:8])
        if got == "fail":
            raise RuntimeError("down")
        return [{"properties": {"時間コード": int(c), "常時観測点コード": 1}, "geometry": {"coordinates": [[139.0, 35.0]]}}
                for c in sorted(got) if t0 <= c <= t1]
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
assert len(names(rel)) == 2 * 2 * 28 + 2 * 2 * 88, len(names(rel))

# 2. a recent day missing one hour is not uploaded (retried later); an old one near expiry is kept
def gap(layer, road, day):
    s = full(layer, road, day)
    if day in (D(1), D(87)) and "1h_img" in layer:
        s = {c for c in s if not c.endswith("0500")}
    return s
code, rel, out = run(gap)
assert code == 0, out
assert f"cctv_1h_road3_{D(1)}.csv.gz" not in names(rel), "partial recent day must wait"
assert f"cctv_1h_road3_{D(87)}.csv.gz" in names(rel), "partial day near expiry is kept"
assert "kept incomplete" in out

# 3. day already gone from the source (reference layer empty): nothing of that day is uploaded
def gone(layer, road, day):
    return set() if day == D(87) else full(layer, road, day)
code, rel, out = run(gone)
assert not any(D(87) in n and "_1h_" in n for n in names(rel)), sorted(n for n in names(rel) if D(87) in n)

# 4. a legitimately empty layer (no CCTV counters on expressways in Kanto) waits, then is kept near expiry
def empty_layer(layer, road, day):
    return set() if (layer.endswith("5m_img") and road == 1) else full(layer, road, day)
code, rel, out = run(empty_layer)
assert f"cctv_5m_road1_{D(27)}.csv.gz" in names(rel) and f"cctv_5m_road1_{D(1)}.csv.gz" not in names(rel)

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
assert len(set(fetched)) == 4, sorted(set(fetched))  # 2 days per kind
print("test_collect: ALL PASS")
