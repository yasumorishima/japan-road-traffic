"""Offline tests for amedas.py with a fake JMA site and fake releases."""
import csv, gzip, io, os, sys, types
from contextlib import redirect_stdout
from datetime import datetime
sys.path.insert(0, os.path.dirname(__file__))
import amedas, collect
amedas_get = amedas.get

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=amedas.JST)   # last complete day 10-07, window 09-29..10-07
STATIONS = {"11001": {"type": "C", "elems": "11112010", "lat": [45, 31.2], "lon": [141, 56.1], "alt": 26,
                      "kjName": "宗谷岬", "knName": "ソウヤミサキ", "enName": "Cape Soya"},
            "44132": {"type": "A", "elems": "11111111", "lat": [35, 41.5], "lon": [139, 45.0], "alt": 25,
                      "kjName": "東京", "knName": "トウキョウ", "enName": "Tokyo"}}
STATIONS.update({str(50000 + i): dict(STATIONS["11001"]) for i in range(1100)})


def station_map(obs):
    rain = 2.5 if obs.endswith("1200") else 0.0
    return {"11001": {"temp": [16.2, 0], "precipitation1h": [rain, 0], "wind": [3.1, 0], "windDirection": [13, 0]},
            "44132": {"temp": [20.0, 0], "precipitation1h": [None, 6], "snow": [None, 5], "weather": [0, 0]}}


def run(missing=(), releases=None, extra_element=False, upload_broken=(), argv=(), thin=(), broken=()):
    """missing: map times (YYYYMMDDhh) the fake site answers 404. Returns (exit, releases, stdout, files)."""
    releases = releases if releases is not None else {}
    files, out = {}, io.StringIO()

    def get(url, tries=3):
        if url.endswith("amedastable.json"):
            if "table" in broken:
                raise RuntimeError("HTTP 503")
            return STATIONS
        key = url.rsplit("/", 1)[1][:10]
        if key in missing:
            return None
        if key in broken:
            raise RuntimeError("HTTP 503")
        m = station_map(key + "00")
        if key in thin:
            m = {"11001": m["11001"]}
        else:
            m.update({str(60000 + i): {"temp": [10.0, 0]} for i in range(18)})
        if extra_element:
            m["11001"]["newThing"] = [1, 0]
        return m

    def gh(*args):
        if args[:2] == ("release", "create"):
            releases.setdefault(args[2], set()); return types.SimpleNamespace(returncode=0, stderr="")
        if args[:2] == ("release", "upload"):
            name = os.path.basename(args[3])
            if name in upload_broken:
                return types.SimpleNamespace(returncode=1, stderr="HTTP 502")
            with gzip.open(args[3], "rt", encoding="utf-8") as f:
                files[name] = list(csv.DictReader(f))
            releases[args[2]].add(name); return types.SimpleNamespace(returncode=0, stderr="")
        raise AssertionError(args)

    amedas.get, amedas.PAUSE = get, 0
    collect.gh = gh
    collect.release_assets = lambda tag: set(releases[tag]) if tag in releases else None
    code = 0
    with redirect_stdout(out):
        try:
            amedas.main(list(argv), NOW)
        except SystemExit as e:
            code = e.code
    return code, releases, out.getvalue(), files


def test_full_window():
    code, rel, out, files = run()
    assert code == 0, out
    days = sorted(n for n in files if not n.startswith("amedas_stations"))
    assert days == [f"amedas_202609{d}.csv.gz" for d in (29, 30)] + [f"amedas_2026100{d}.csv.gz" for d in range(1, 8)], days
    assert set(rel) == {"amedas-202609", "amedas-202610"}
    assert "amedas_stations_202609.csv.gz" in rel["amedas-202609"] and "amedas_stations_202610.csv.gz" in rel["amedas-202610"]
    rows = files["amedas_20261007.csv.gz"]
    assert len(rows) == 24 * 20
    assert rows[0]["obs_time_jst"] == "202610070100" and rows[0]["hour_start_jst"] == "202610070000"
    assert rows[-1]["obs_time_jst"] == "202610080000" and rows[-1]["hour_start_jst"] == "202610072300"
    noon = [r for r in rows if r["obs_time_jst"] == "202610071200" and r["station_id"] == "11001"][0]
    assert noon["precip_1h_mm"] == "2.5" and noon["precip_1h_mm_aqc"] == "0" and noon["hour_start_jst"] == "202610071100"
    assert "newThing" not in rows[0]
    tokyo = [r for r in rows if r["station_id"] == "44132"][0]
    assert tokyo["precip_1h_mm"] == "" and tokyo["precip_1h_mm_aqc"] == "6" and tokyo["temp_c"] == "20.0"
    assert tokyo["wind_ms"] == "" and tokyo["wind_ms_aqc"] == ""   # element not observed there
    st = {r["station_id"]: r for r in files["amedas_stations_202610.csv.gz"]}
    assert st["11001"]["lat"] == "45.52" and st["11001"]["lon"] == "141.935" and st["44132"]["name_en"] == "Tokyo"


def test_nothing_to_do_when_all_in():
    _, rel, _, _ = run()
    code, rel2, out, files = run(releases={k: set(v) for k, v in rel.items()})
    assert code == 0 and files == {}, out


def test_missing_map_inside_window_is_retried():
    code, rel, out, files = run(missing={"2026100512"})
    assert code == 0
    assert "amedas_20261005.csv.gz" not in files and "retry later" in out
    assert "amedas_20261006.csv.gz" in files


def test_midnight_map_belongs_to_the_day_before():
    code, rel, out, files = run(missing={"2026100600"})
    assert "amedas_20261005.csv.gz" not in files and "amedas_20261006.csv.gz" in files, out


def test_near_expiry_day_kept_as_it_is():
    code, rel, out, files = run(missing={"2026092903", "2026092906"})
    assert code == 0
    rows = files["amedas_20260929.csv.gz"]
    assert len(rows) == 22 * 20 and "missing 0300, 0600" in out, out


def test_expired_day_never_empty():
    gone = {f"20260929{h:02d}" for h in range(1, 24)} | {"2026093000"}
    code, rel, out, files = run(missing=gone)
    assert code == 0 and "amedas_20260929.csv.gz" not in files and "not kept" in out


def test_unknown_element_is_left_out_and_red():
    code, rel, out, files = run(extra_element=True)
    assert code == 1 and "newThing" in out
    rows = files["amedas_20261007.csv.gz"]
    assert len(rows) == 24 * 20 and "newThing" not in rows[0]


def test_second_oldest_day_with_a_gap_is_retried():
    code, rel, out, files = run(missing={"2026093012"})
    assert code == 0 and "amedas_20260930.csv.gz" not in files and "amedas_20260929.csv.gz" in files, out


def test_thin_map_counts_as_missing():
    code, rel, out, files = run(thin={"2026100512", "2026092912"})
    assert "amedas_20261005.csv.gz" not in files and "counted as missing" in out
    assert len(files["amedas_20260929.csv.gz"]) == 23 * 20 and "missing 1200" in out


def test_a_failing_day_does_not_stop_later_days():
    code, rel, out, files = run(broken={"2026093005"})
    assert code == 1 and "amedas_20260930.csv.gz" in out
    assert "amedas_20260930.csv.gz" not in files and "amedas_20261007.csv.gz" in files


def test_get_retries_and_maps_404():
    import urllib.error
    real = amedas_get
    calls = []
    class Resp:
        def __init__(self, body): self.body = body
        def read(self): return self.body
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def opener(seq):
        def urlopen(req, timeout=None):
            calls.append(1)
            x = seq.pop(0)
            if isinstance(x, Exception):
                raise x
            return Resp(x)
        return urlopen
    saved = (amedas.urllib.request.urlopen, amedas.time.sleep)
    amedas.time.sleep = lambda s: None
    try:
        amedas.urllib.request.urlopen = opener([urllib.error.HTTPError("u", 404, "nf", {}, None)])
        assert real("https://x/u") is None
        amedas.urllib.request.urlopen = opener([ConnectionResetError("reset"), urllib.error.HTTPError("u", 503, "x", {}, None), b'{"a": 1}'])
        assert real("https://x/u") == {"a": 1}
        amedas.urllib.request.urlopen = opener([b"not json"] * 3)
        try:
            real("https://x/u"); raise AssertionError("no raise")
        except RuntimeError as e:
            assert "u" in str(e)
    finally:
        amedas.urllib.request.urlopen, amedas.time.sleep = saved


def test_upload_failure_is_red():
    code, rel, out, files = run(upload_broken={"amedas_20261003.csv.gz"})
    assert code == 1 and "amedas_20261003.csv.gz" in out and "amedas_20261004.csv.gz" in files


def test_dry_run_fetches_nothing():
    code, rel, out, files = run(argv=["--dry-run"])
    assert code == 0 and files == {} and rel == {} and out.count("would fetch 2026") == 9, out
    assert out.count("would fetch the station table") == 2, out


def test_before_one_am_yesterday_is_not_complete():
    global NOW
    saved, NOW = NOW, datetime(2026, 10, 8, 0, 30, tzinfo=amedas.JST)
    try:
        code, rel, out, files = run()
        assert "amedas_20261007.csv.gz" not in files and "amedas_20261006.csv.gz" in files
    finally:
        NOW = saved


def test_station_table_upload_failure_is_red():
    code, rel, out, files = run(upload_broken={"amedas_stations_202610.csv.gz"})
    assert code == 1 and "amedas_stations_202610.csv.gz" in out and "amedas_20261007.csv.gz" in files


def test_station_table_fetch_failure_is_red_and_days_continue():
    code, rel, out, files = run(broken={"table"})
    assert code == 1 and "station table" in out and "amedas_20261007.csv.gz" in files
    assert not any(n.startswith("amedas_stations") for n in files)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} tests passed")
