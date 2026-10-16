"""Tests against records the API really sent (tests/fixtures_live_20261005.json, one per layer with counts and,
where the layer had one, one with blanks): every field reaches the CSV, and build_kaggle.py turns the four
layers into the English tables without losing a count, and make_kaggle_meta.py describes every column."""
import csv, json, os, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch, amedas, enrich

FIX = json.load(open(os.path.join(HERE, "..", "tests", "fixtures_live_20261005.json"), encoding="utf-8"))
ASSET = {"t_travospublic_measure_5m": "loop_5m", "t_travospublic_measure_5m_img": "cctv_5m",
         "t_travospublic_measure_1h": "loop_1h", "t_travospublic_measure_1h_img": "cctv_1h"}
assert set(FIX) == set(ASSET)

# 1. each real record maps field by field onto its layer's columns
for layer, recs in FIX.items():
    for f in recs:
        r = dict(zip(fetch.columns(layer), fetch.row(layer, f)))
        for k, v in f["properties"].items():
            if k in fetch.REDUNDANT:
                continue
            assert r[k] == ("" if v is None else v), (layer, k, r[k], v)
        counts = [k for k in fetch.SCHEMA[layer] if "交通量" in k]
        assert counts, layer

# 2. build_kaggle on files written from those records
try:
    import numpy as np, pandas as pd
except ImportError:
    print("test_fixtures: pandas not installed, skipped the build_kaggle part"); sys.exit(0)
with tempfile.TemporaryDirectory() as tmp:
    src = os.path.join(tmp, "in"); os.makedirs(src)
    ids = set()
    for layer, recs in FIX.items():
        fetch.write_csv(os.path.join(src, f"{ASSET[layer]}_road3_20261005.csv.gz"), [fetch.row(layer, f) for f in recs], layer)
        sensor = ASSET[layer].split("_")[0]
        ids |= {(f["properties"]["常時観測点コード"], sensor) for f in recs}
    # one AMeDAS station near the test counters (139.6, 35.4) with the day of the fixtures, and the station table
    wdir = os.path.join(tmp, "wx"); os.makedirs(wdir)
    wrows = [{"obs_time_jst": f"20261005{h + 1:02d}00" if h < 23 else "202610060000",
              "hour_start_jst": f"20261005{h:02d}00", "station_id": 46106, "temp_c": 20 + h / 10,
              "temp_c_aqc": 0 if h != 8 else 5,  # the fixtures' hour (08:00) has a flagged temperature
              "precip_1h_mm": 1.5 + h, "precip_1h_mm_aqc": 0, "wind_ms": 3.0, "wind_ms_aqc": 0}
             for h in range(24)]
    amedas.write_csv(os.path.join(wdir, "amedas_20261005.csv.gz"), wrows, amedas.COLUMNS)
    amedas.write_csv(os.path.join(wdir, "amedas_stations_202610.csv.gz"),
                     [{"station_id": 46106, "type": "A", "elems": "11111111", "lat": 35.43833, "lon": 139.65167,
                       "alt_m": 39, "name_ja": "横浜", "name_kana": "ヨコハマ", "name_en": "Yokohama"}],
                     amedas.STATION_COLUMNS)
    WX = ["--weather", wdir]
    counters = os.path.join(tmp, "counters.csv")
    HEADER = ["counter_id", "sensor", "road_type", "longitude", "latitude", "prefecture", "municipality_code",
              "municipality", "town", "in_5min_box", "last_seen"]
    with open(counters, "w", newline="", encoding="utf-8") as g:
        w = csv.writer(g); w.writerow(HEADER)
        w.writerows([[i, s, 3, 139.6, 35.4, "神奈川県", "14101", "横浜市鶴見区", "", True, "2026-10-05"] for i, s in sorted(ids)])
    out = os.path.join(tmp, "out")
    build = [sys.executable, os.path.join(HERE, "build_kaggle.py"), src, out, "--counters", counters, *WX]
    r = subprocess.run(build, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    h = pd.read_parquet(os.path.join(out, "hourly_2026-10_01-10.parquet"))
    m = pd.read_parquet(os.path.join(out, "five_minute_kanto_2026-10_01-10.parquet"))
    for df, layers in ((h, ["t_travospublic_measure_1h", "t_travospublic_measure_1h_img"]),
                       (m, ["t_travospublic_measure_5m", "t_travospublic_measure_5m_img"])):
        for layer in layers:
            sensor = ASSET[layer].split("_")[0]
            sub = df[df.sensor == sensor].set_index("counter_id")
            for f in FIX[layer]:
                p = f["properties"]; row = sub.loc[p["常時観測点コード"]]
                small = next(v for k, v in p.items() if k.startswith("上り・小型交通量"))
                got = row["up_small"]
                assert (pd.isna(got) and small in (None, "")) or got == small, (layer, got, small)
                tot = next((v for k, v in p.items() if k.startswith("上り・自動車交通量")), None)
                if sensor == "cctv":
                    assert (pd.isna(row["up_total"]) and tot in (None, "")) or row["up_total"] == tot, (layer, row["up_total"], tot)
                else:
                    assert pd.isna(row["up_total"])
    assert str(h.up_missing_processing.dtype) == "Int8" and str(m.cam_preset_position.dtype) == "boolean"
    assert len(h) == len(FIX["t_travospublic_measure_1h"]) + len(FIX["t_travospublic_measure_1h_img"])
    assert sorted(os.listdir(out)) == ["calendar.csv", "census_2021_counters.csv", "counters.csv",
                                       "five_minute_kanto_2026-10_01-10.parquet", "hourly_2026-10_01-10.parquet",
                                       "road_graph_2021.csv", "weather_hourly_2026-10_01-10.parquet", "weather_stations.csv"]
    # ten-day chunks: names do not depend on which days are present yet
    import build_kaggle
    assert [build_kaggle.chunk(d) for d in ("20261001", "20261010", "20261011", "20261020", "20261021", "20261031",
            "20260221", "20280229", "20260930")] == ["2026-10_01-10", "2026-10_01-10", "2026-10_11-20", "2026-10_11-20",
            "2026-10_21-31", "2026-10_21-31", "2026-02_21-28", "2028-02_21-29", "2026-09_21-30"]
    # 3. Kaggle metadata: one resource per file, every column described; an undescribed column stops it
    meta = [sys.executable, os.path.join(HERE, "make_kaggle_meta.py")]
    settings = os.path.join(tmp, "settings.json")
    r = subprocess.run(meta + [out, "--settings", settings], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    md = json.load(open(os.path.join(out, "dataset-metadata.json"), encoding="utf-8"))
    FILES = ["calendar.csv", "census_2021_counters.csv", "counters.csv", "five_minute_kanto_2026-10_01-10.parquet",
             "hourly_2026-10_01-10.parquet", "road_graph_2021.csv", "weather_hourly_2026-10_01-10.parquet",
             "weather_stations.csv"]
    assert sorted(x["path"] for x in md["resources"]) == FILES
    for x in md["resources"]:
        names = [f["name"] for f in x["schema"]["fields"]]
        actual = list(pd.read_csv(os.path.join(out, x["path"]), nrows=0).columns) if x["path"].endswith(".csv")             else list(pd.read_parquet(os.path.join(out, x["path"])).columns)
        assert names == actual and all(f["description"] for f in x["schema"]["fields"]), x["path"]
    st = json.load(open(settings, encoding="utf-8"))
    assert set(st["files"]) == {x["path"] for x in md["resources"]}
    assert md["licenses"] == [{"name": "CC-BY-4.0"}] and len(md["keywords"]) <= 5
    # the census table: section numbers keep their leading zero, one row per counter, speeds plausible
    cen = pd.read_csv(os.path.join(out, "census_2021_counters.csv"), dtype={"census_section": str})
    assert cen.census_section.str.fullmatch(r"\d{11}").all() and not cen.counter_id.duplicated().any()
    assert len(cen) == 365, len(cen)  # the count stated in README.md, kaggle/description.md and make_kaggle_meta.py
    assert cen.filter(like="speed").stack().dropna().between(1, 130).all() and (cen.sensor == "loop").all()
    known = set(pd.read_csv(os.path.join(HERE, "..", "data", "counters.csv")).query("sensor == \"loop\"").counter_id)
    assert set(cen.counter_id) <= known
    # where each counter sits on the census network: a stretch of its unit, in the same prefecture, of positive length
    for c in ("placed_from_section", "placed_to_section"):
        cen[c] = cen[c].astype(str).str.zfill(11)
        assert cen[c].str.fullmatch(r"\d{11}").all() and (cen[c].str[:2] == cen.census_section.str[:2]).all(), c
    assert (cen.placed_from_section <= cen.placed_to_section).all() and cen.placed_km.between(0.1, 30).all()
    assert cen.placed_in_municipality.dtype == bool and cen.placed_in_municipality.mean() > 0.95
    # the road graph: pairs of tied counters, each once, within 60 km. Only the files themselves are read here: this
    # test runs before every collect, and counters.csv takes each seen counter's coordinates from the API daily, so a
    # check against it could stop collection over a moved counter (line_km is checked when the graph is built)
    g = pd.read_csv(os.path.join(out, "road_graph_2021.csv"))
    assert list(g.columns) == ["counter_a", "counter_b", "road_km", "line_km", "same_route", "direct"]
    assert (g.counter_a < g.counter_b).all() and not g.duplicated(["counter_a", "counter_b"]).any()
    assert set(g.counter_a) | set(g.counter_b) <= set(cen.counter_id) and g.road_km.between(0.1, 60).all()
    assert g.direct.dtype == bool and g.same_route.dtype == bool and len(g) > 1000 and g.line_km.ge(0).all()
    half = cen.set_index("counter_id").placed_km / 2
    over = g.line_km > g.road_km + half.loc[g.counter_a].to_numpy() + half.loc[g.counter_b].to_numpy() + 0.5
    assert over.sum() <= 0.01 * len(g), over.sum()  # a straight line cannot be much longer than the road
    same = cen.set_index("counter_id").route_ja
    assert (g.same_route == (same.loc[g.counter_a].to_numpy() == same.loc[g.counter_b].to_numpy())).all()
    for name in ("census_2021_counters.csv", "road_graph_2021.csv"):
        os.rename(os.path.join(out, name), os.path.join(tmp, name))
        r = subprocess.run(meta + [out, "--settings", settings], capture_output=True, text=True)
        assert r.returncode != 0 and name in (r.stderr + r.stdout), (name, r.stderr)
        os.rename(os.path.join(tmp, name), os.path.join(out, name))
    h.assign(extra=1).to_parquet(os.path.join(out, "hourly_2026-10_01-10.parquet"), index=False)
    r = subprocess.run(meta + [out, "--settings", settings], capture_output=True, text=True)
    assert r.returncode != 0 and "without a description" in (r.stderr + r.stdout), r.stderr
    # 4. a counter missing from counters.csv fails the build
    with open(counters, "w", newline="", encoding="utf-8") as g:
        w = csv.writer(g); w.writerow(HEADER)
        w.writerows([[i, s, 3, 139.6, 35.4, "神奈川県", "14101", "横浜市鶴見区", "", True, "2026-10-05"] for i, s in sorted(ids)[1:]])
    r = subprocess.run(build[:3] + [out + "2", "--counters", counters, *WX], capture_output=True, text=True)
    assert r.returncode != 0 and "not in" in (r.stderr + r.stdout), r.stderr

    # 5. derived columns, recomputed here from the source's columns
    def write_counters(rows):
        with open(counters, "w", newline="", encoding="utf-8") as g:
            w = csv.writer(g); w.writerow(HEADER); w.writerows(rows)
    def full(pref="神奈川県", code="14101"):
        return [[i, s, 3, 139.6, 35.4, pref, code, "x", "", True, "2026-10-05"] for i, s in sorted(ids)]
    # a flagged permanent-counter hour that still carries counts must get no vehicles
    src5 = os.path.join(tmp, "in5"); os.makedirs(src5)
    for layer, recs in FIX.items():
        rows = [fetch.row(layer, f) for f in recs]
        if layer == "t_travospublic_measure_1h":
            cols = fetch.columns(layer)
            j, k = cols.index("下り・ループ異常"), cols.index("下り・小型交通量")
            assert rows[0][k] not in ("", None)
            rows[0][j] = "1"
            rows[0][cols.index("上り・車種判別不能交通量")] = ""  # one of three parts blank: no up total
            flagged_id = recs[0]["properties"]["常時観測点コード"]
        if layer == "t_travospublic_measure_5m":
            cols = fetch.columns(layer)
            assert rows[0][cols.index("上り・小型交通量")] not in ("", None)
            rows[0][cols.index("上り・欠測")] = ""  # a flag the source left blank: not known to be clean
            unknown_id = recs[0]["properties"]["常時観測点コード"]
        fetch.write_csv(os.path.join(src5, f"{ASSET[layer]}_road3_20261005.csv.gz"), rows, layer)
    hol = os.path.join(tmp, "hol.csv")
    with open(hol, "w", encoding="utf-8") as g:
        g.write("date,name\n2026-10-05,スポーツの日\n2027-01-01,元日\n")
    write_counters(full())
    out5 = os.path.join(tmp, "out5")
    r = subprocess.run(build[:2] + [src5, out5, "--counters", counters, *WX, "--holidays", hol], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for name in ("hourly_2026-10_01-10.parquet", "five_minute_kanto_2026-10_01-10.parquet"):
        d = pd.read_parquet(os.path.join(out5, name))
        five = name.startswith("five")
        assert list(d.columns) == (build_kaggle.ORDER + (build_kaggle.STATUS if five else []) + build_kaggle.DERIVED
                                   + ([] if five else enrich.CAL_ROW + enrich.BASE_ROW + enrich.WEATHER_ROW)), name
        if not five:
            # 2026-10-05 is a holiday in hol.csv after a weekend: a run of three days off
            assert d.day_off.all() and (d.days_off_in_run == 3).all() and d.customary_break.isna().all(), name
            # the station is about 6 km away: within every limit; rain of 08:00-09:00 is 9.5 mm
            assert (d.time_jst.dt.hour == 8).all() and (d.precip_mm == 9.5).all() and (d.wind_ms == 3.0).all()
            assert d.temp_c.isna().all() and d.snow_depth_cm.isna().all()  # flagged in the source; no snow station
            assert d.baseline_vehicles.isna().all() and d.vs_baseline.isna().all()  # one day: no earlier days
        loop = d.sensor == "loop"
        for side in ("up", "down"):
            fl = d[[f"{side}_{x}" for x in ("power_failure", "loop_fault", "ultrasonic_fault", "missing")]]
            parts = d[[f"{side}_small", f"{side}_large", f"{side}_unclassified"]].astype("float64").sum(axis=1, min_count=3)
            want = parts.where(loop & fl.notna().all(axis=1) & ~fl.fillna(True).any(axis=1), float("nan")).where(loop, d[f"{side}_total"].astype("float64"))
            got = d[f"{side}_vehicles"].astype("float64")
            assert ((want == got) | (want.isna() & got.isna())).all(), (name, side)
        both = d.up_vehicles.astype("float64") + d.down_vehicles.astype("float64")
        assert ((both == d.vehicles.astype("float64")) | (both.isna() & d.vehicles.isna())).all(), name
        assert d.vehicles.notna().any() and d.loc[~loop, "vehicles"].notna().any(), name  # CCTV totals reach it
        F8 = d.loc[loop, [c for c in d if c.endswith(("_power_failure", "_loop_fault", "_ultrasonic_fault", "_missing"))]]
        assert F8.shape[1] == 8 and d.flagged[~loop].isna().all()
        anyf = F8.fillna(False).any(axis=1)
        exp = [True if x else (False if k else None) for x, k in zip(anyf, F8.notna().all(axis=1))]
        assert exp == [None if pd.isna(x) else bool(x) for x in d.flagged[loop]], name
        assert (d.weekday == 0).all() and d.is_holiday.all(), name  # 2026-10-05 is a Monday, a holiday in hol.csv
        assert (d.prefecture == "神奈川県").all() and (d.prefecture_en == "Kanagawa").all(), name
    row = pd.read_parquet(os.path.join(out5, "hourly_2026-10_01-10.parquet")).query("sensor == 'loop' and counter_id == @flagged_id")
    assert len(row) == 1 and bool(row.flagged.iloc[0]) and pd.isna(row.down_vehicles.iloc[0]) and pd.isna(row.vehicles.iloc[0])
    assert pd.isna(row.up_vehicles.iloc[0]) and pd.notna(row.up_small.iloc[0]) and pd.notna(row.down_small.iloc[0])  # raw counts stay
    row = pd.read_parquet(os.path.join(out5, "five_minute_kanto_2026-10_01-10.parquet")).query("sensor == 'loop' and counter_id == @unknown_id")
    assert len(row) >= 1 and row.up_vehicles.isna().all() and row.down_vehicles.notna().all() and row.flagged.isna().all()
    # the real holiday list: 2026-10-05 is not a holiday; build_kaggle reads data/holidays_jp.csv by default
    real = os.path.join(tmp, "out6")
    r = subprocess.run(build[:3] + [real, "--counters", counters, *WX], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert not pd.read_parquet(os.path.join(real, "hourly_2026-10_01-10.parquet")).is_holiday.any()
    hl = pd.read_csv(os.path.join(HERE, "..", "data", "holidays_jp.csv"), dtype=str)
    assert {"2026-07-20", "2026-08-11", "2026-09-21", "2026-09-22", "2026-09-23", "2026-10-12"} <= set(hl.date)
    # a list whose last year is the data's year passes; one that ends before the data stops the build
    with open(hol, "w", encoding="utf-8") as g:
        g.write("date,name\n2026-01-01,元日\n")
    r = subprocess.run(build[:3] + [os.path.join(tmp, "out10"), "--counters", counters, *WX, "--holidays", hol], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    with open(hol, "w", encoding="utf-8") as g:
        g.write("date,name\n2025-01-01,元日\n")
    r = subprocess.run(build[:3] + [os.path.join(tmp, "out7"), "--counters", counters, *WX, "--holidays", hol], capture_output=True, text=True)
    assert r.returncode != 0 and "holiday list ends" in (r.stderr + r.stdout), r.stderr
    # a prefecture name that disagrees with its code stops the build; no municipality at all gives empty names
    write_counters(full(code="13101"))
    r = subprocess.run(build[:3] + [os.path.join(tmp, "out8"), "--counters", counters, *WX], capture_output=True, text=True)
    assert r.returncode != 0 and "does not match" in (r.stderr + r.stdout), r.stderr
    write_counters(full(pref="", code=""))
    r = subprocess.run(build[:3] + [os.path.join(tmp, "out9"), "--counters", counters, *WX], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    d = pd.read_parquet(os.path.join(tmp, "out9", "hourly_2026-10_01-10.parquet"))
    assert d.prefecture.isna().all() and d.prefecture_en.isna().all()
    c9 = pd.read_csv(os.path.join(tmp, "out9", "counters.csv"), dtype=str, keep_default_na=False)
    assert (c9.prefecture_en == "").all() and (c9.municipality_en == "").all()
    # English names in the published counters.csv, next to the Japanese ones
    write_counters(full())
    r = subprocess.run(build[:3] + [os.path.join(tmp, "out11"), "--counters", counters, *WX], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    c11 = pd.read_csv(os.path.join(tmp, "out11", "counters.csv"), dtype=str, keep_default_na=False)
    assert list(c11.columns) == ["counter_id", "sensor", "road_type", "longitude", "latitude", "prefecture", "prefecture_en",
                                 "municipality_code", "municipality", "municipality_en", "town", "in_5min_box", "last_seen"
                                 ] + enrich.STATION_COLS
    assert (c11.precip_station_id == "46106").all() and (c11.snow_station_id == "").all()  # snow: not in the station's rows
    assert (c11.prefecture_en == "Kanagawa").all() and (c11.municipality_en == "Yokohama-shi Tsurumi-ku").all()
    assert (c11.municipality_code == "14101").all() and len(c11) == len(ids)
    assert c11.drop(columns=["prefecture_en", "municipality_en"] + enrich.STATION_COLS).equals(pd.read_csv(counters, dtype=str, keep_default_na=False))
    write_counters([[i, s, 3, 139.6, 35.4, "神奈川", "14101", "x", "", True, "2026-10-05"] for i, s in sorted(ids)])
    r = subprocess.run(build[:3] + [os.path.join(tmp, "out13"), "--counters", counters, *WX], capture_output=True, text=True)
    assert r.returncode != 0 and "does not match" in (r.stderr + r.stdout), r.stderr  # not one of the 47: places() stops first
    write_counters(full())
    names = os.path.join(tmp, "names.csv")
    with open(names, "w", encoding="utf-8") as g:
        g.write("municipality_code,prefecture,municipality,municipality_kana,municipality_en\n01100,北海道,札幌市,サッポロシ,Sapporo-shi\n")
    r = subprocess.run(build[:3] + [os.path.join(tmp, "out12"), "--counters", counters, *WX, "--names", names], capture_output=True, text=True)
    assert r.returncode != 0 and "no English name" in (r.stderr + r.stdout), r.stderr
    # the committed table: one row per code, every name in the expected shape, known hard cases right
    mt = pd.read_csv(os.path.join(HERE, "..", "data", "municipalities_en.csv"), dtype=str)
    assert mt.municipality_code.is_unique and mt.municipality_code.str.fullmatch(r"\d{5}").all()
    assert mt.municipality_en.str.fullmatch(r"[A-Z][A-Za-z' -]*-(shi|ku|cho|machi|mura|son)").all()
    known = dict(zip(mt.municipality, mt.municipality_en))
    for k, v in {"札幌市中央区": "Sapporo-shi Chuo-ku", "豊浦町": "Toyoura-cho", "中之条町": "Nakanojo-machi",
                 "北谷町": "Chatan-cho", "広尾町": "Hiroo-cho", "千代田区": "Chiyoda-ku", "七飯町": "Nanae-cho"}.items():
        assert known[k] == v, (k, known[k])
    cs = pd.read_csv(os.path.join(HERE, "..", "data", "counters.csv"), dtype={"municipality_code": str})
    assert set(cs.municipality_code.dropna()) <= set(mt.municipality_code)
    # every derived column is described
    r = subprocess.run(meta + [out5, "--settings", settings], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
print("test_fixtures: ALL PASS")
