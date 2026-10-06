"""Offline tests for fetch.py: an oversized range is split until it fits, and every code is fetched once."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
import fetch

calls = []
def fake_get(layer, road, t0, t1, bbox):
    calls.append((t0, t1))
    cs = [c for c in fetch.codes(t0[:8], 5) if t0 <= c <= t1]
    if len(cs) > 3:
        return None  # pretend the response is over the 6 MB cap
    return [{"properties": {"時間コード": int(c), "常時観測点コード": 1}, "geometry": {"coordinates": [[139.0, 35.0]]}} for c in cs]

fetch.get = fake_get
rows = []
cs = fetch.codes("20261005", 5)
fetch.fetch_codes("x", 3, cs[:12], fetch.KANTO, rows)
got = [r[0] for r in rows]
assert got == [int(c) for c in cs[:12]], got
span = lambda a, b: len([c for c in cs if a <= c <= b])
assert calls[0] == (cs[0], cs[11]) and len(calls) > 1  # the oversized range was tried, then split
assert len(set(got)) == 12
assert len(fetch.codes("20261005", 60)) == 24 and fetch.codes("20261005", 60)[-1] == "202610052300"

# a single code that is still too large must raise, not loop or drop it
fetch.get = lambda *a: None
try:
    fetch.fetch_codes("x", 3, cs[:1], fetch.KANTO, [])
    raise AssertionError("expected RuntimeError")
except RuntimeError:
    pass
print("test_fetch: ALL PASS")
