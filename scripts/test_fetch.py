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
    return [{"properties": dict({k: 0 for k in fetch.SCHEMA[layer]}, 時間コード=int(c), 常時観測点コード=1),
             "geometry": {"coordinates": [[139.0, 35.0]]}} for c in cs]

fetch.get = fake_get
rows = []
cs = fetch.codes("20261005", 5)
fetch.fetch_codes("t_travospublic_measure_5m", 3, cs[:12], fetch.KANTO, rows)
got = [r[0] for r in rows]
assert got == [int(c) for c in cs[:12]], got
span = lambda a, b: len([c for c in cs if a <= c <= b])
assert calls[0] == (cs[0], cs[11]) and len(calls) > 1  # the oversized range was tried, then split
assert len(set(got)) == 12
assert len(fetch.codes("20261005", 60)) == 24 and fetch.codes("20261005", 60)[-1] == "202610052300"

# a single code that is still too large must raise, not loop or drop it
fetch.get = lambda *a: None
try:
    fetch.fetch_codes("t_travospublic_measure_5m", 3, cs[:1], fetch.KANTO, [])
    raise AssertionError("expected RuntimeError")
except RuntimeError:
    pass
# rows follow each layer's own field names: a CCTV 5-minute record as the API sent it on 2026-10-05
cctv = {"地方整備局等番号": 82, "開発建設部／都道府県コード": "", "常時観測点コード": 2810010, "収集時間フラグ（5分間／1時間）": "1",
        "観測年月日": 20261005, "時間帯": 800, "上り・自動車交通量（集計値）": 55, "上り・小型交通量（集計値）": 44,
        "上り・大型交通量（集計値）": 11, "上り・小型大型判別不能交通量（集計値）": 0, "下り・自動車交通量（集計値）": 52,
        "下り・小型交通量（集計値）": 50, "下り・大型交通量（集計値）": 2, "下り・小型大型判別不能交通量（集計値）": 0,
        "カメラプリセット位置": "0", "気象影響による映像不良": "", "照度不足": "", "突発事象（交通事故等）": "", "サーバの稼働": "0",
        "カメラの映像受信": "", "映像のデコード処理": "", "デコード映像から映像解析機能への取込加工処理の失敗": "",
        "映像解析機能のフリーズ": "", "その他エラー": "", "道路種別": "3", "時間コード": 202610050800}
r = dict(zip(fetch.columns("t_travospublic_measure_5m_img"), fetch.row("t_travospublic_measure_5m_img", {"properties": cctv, "geometry": {"coordinates": [[139.1, 35.2]]}})))
assert r["上り・小型交通量（集計値）"] == 44 and r["下り・自動車交通量（集計値）"] == 52 and r["経度"] == 139.1, r
# a field the schema does not know, or one it expects but the source dropped, stops the fetch
for bad in (dict(cctv, 新しい項目=1), {k: v for k, v in cctv.items() if k != "上り・小型交通量（集計値）"}):
    try:
        fetch.row("t_travospublic_measure_5m_img", {"properties": bad, "geometry": {"coordinates": [[0, 0]]}})
        raise AssertionError("expected a schema error")
    except RuntimeError as e:
        assert "schema changed" in str(e)
# the loop schema applied to a CCTV record is refused, not written as empty counts
try:
    fetch.row("t_travospublic_measure_5m", {"properties": cctv, "geometry": {"coordinates": [[0, 0]]}})
    raise AssertionError("expected a schema error")
except RuntimeError:
    pass

# the real get(): a response whose numberMatched exceeds numberReturned is treated like an oversized one
import importlib, io, json as _json
real = importlib.reload(fetch)
real.PAUSE = 0
def fake_urlopen(url, timeout=0):
    body = {"features": [{"properties": {}}], "numberMatched": 5, "numberReturned": 1}
    if "202610050005" in urllib_unquote(url):
        body = {"features": [{"properties": {}}], "numberMatched": 1, "numberReturned": 1}
    return io.BytesIO(_json.dumps(body).encode())
from urllib.parse import unquote as urllib_unquote
real.urllib.request.urlopen = fake_urlopen
assert real.get("x", 3, "202610050000", "202610050000", real.KANTO) is None
assert real.get("x", 3, "202610050005", "202610050005", real.KANTO) == [{"properties": {}}]
print("test_fetch: ALL PASS")
