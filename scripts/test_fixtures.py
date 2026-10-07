"""Tests against records the API really sent (tests/fixtures_live_20261005.json, one per layer with counts and,
where the layer had one, one with blanks): every field reaches the CSV, and build_kaggle.py turns the four
layers into the English tables without losing a count, and make_kaggle_meta.py describes every column."""
import csv, json, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch

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
    import pandas as pd
except ImportError:
    print("test_fixtures: pandas not installed, skipped the build_kaggle part"); sys.exit(0)
with tempfile.TemporaryDirectory() as tmp:
    src = os.path.join(tmp, "in"); os.makedirs(src)
    ids = set()
    for layer, recs in FIX.items():
        fetch.write_csv(os.path.join(src, f"{ASSET[layer]}_road3_20261005.csv.gz"), [fetch.row(layer, f) for f in recs], layer)
        sensor = ASSET[layer].split("_")[0]
        ids |= {(f["properties"]["常時観測点コード"], sensor) for f in recs}
    counters = os.path.join(tmp, "counters.csv")
    HEADER = ["counter_id", "sensor", "road_type", "longitude", "latitude", "prefecture", "municipality_code",
              "municipality", "town", "in_5min_box", "last_seen"]
    with open(counters, "w", newline="", encoding="utf-8") as g:
        w = csv.writer(g); w.writerow(HEADER)
        w.writerows([[i, s, 3, 139.6, 35.4, "神奈川県", "14101", "横浜市鶴見区", "", True, "2026-10-05"] for i, s in sorted(ids)])
    out = os.path.join(tmp, "out")
    build = [sys.executable, os.path.join(HERE, "build_kaggle.py"), src, out, "--counters", counters]
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
    assert sorted(os.listdir(out)) == ["counters.csv", "five_minute_kanto_2026-10_01-10.parquet", "hourly_2026-10_01-10.parquet"]
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
    FILES = ["counters.csv", "five_minute_kanto_2026-10_01-10.parquet", "hourly_2026-10_01-10.parquet"]
    assert sorted(x["path"] for x in md["resources"]) == FILES
    for x in md["resources"]:
        names = [f["name"] for f in x["schema"]["fields"]]
        actual = list(pd.read_csv(os.path.join(out, x["path"]), nrows=0).columns) if x["path"].endswith(".csv")             else list(pd.read_parquet(os.path.join(out, x["path"])).columns)
        assert names == actual and all(f["description"] for f in x["schema"]["fields"]), x["path"]
    st = json.load(open(settings, encoding="utf-8"))
    assert set(st["files"]) == {x["path"] for x in md["resources"]}
    assert md["licenses"] == [{"name": "CC-BY-4.0"}] and len(md["keywords"]) <= 5
    h.assign(extra=1).to_parquet(os.path.join(out, "hourly_2026-10_01-10.parquet"), index=False)
    r = subprocess.run(meta + [out, "--settings", settings], capture_output=True, text=True)
    assert r.returncode != 0 and "without a description" in (r.stderr + r.stdout), r.stderr
    # 4. a counter missing from counters.csv fails the build
    with open(counters, "w", newline="", encoding="utf-8") as g:
        w = csv.writer(g); w.writerow(HEADER)
        w.writerows([[i, s, 3, 139.6, 35.4, "神奈川県", "14101", "横浜市鶴見区", "", True, "2026-10-05"] for i, s in sorted(ids)[1:]])
    r = subprocess.run(build[:3] + [out + "2", "--counters", counters], capture_output=True, text=True)
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
        g.write("date,name\n2026-10-05,test\n2027-01-01,元日\n")
    write_counters(full())
    out5 = os.path.join(tmp, "out5")
    r = subprocess.run(build[:2] + [src5, out5, "--counters", counters, "--holidays", hol], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for name in ("hourly_2026-10_01-10.parquet", "five_minute_kanto_2026-10_01-10.parquet"):
        d = pd.read_parquet(os.path.join(out5, name))
        five = name.startswith("five")
        assert list(d.columns) == build_kaggle.ORDER + (build_kaggle.STATUS if five else []) + build_kaggle.DERIVED, name
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
    r = subprocess.run(build[:3] + [real, "--counters", counters], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert not pd.read_parquet(os.path.join(real, "hourly_2026-10_01-10.parquet")).is_holiday.any()
    hl = pd.read_csv(os.path.join(HERE, "..", "data", "holidays_jp.csv"), dtype=str)
    assert {"2026-07-20", "2026-08-11", "2026-09-21", "2026-09-22", "2026-09-23", "2026-10-12"} <= set(hl.date)
    # a list whose last year is the data's year passes; one that ends before the data stops the build
    with open(hol, "w", encoding="utf-8") as g:
        g.write("date,name\n2026-01-01,元日\n")
    r = subprocess.run(build[:3] + [os.path.join(tmp, "out10"), "--counters", counters, "--holidays", hol], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    with open(hol, "w", encoding="utf-8") as g:
        g.write("date,name\n2025-01-01,元日\n")
    r = subprocess.run(build[:3] + [os.path.join(tmp, "out7"), "--counters", counters, "--holidays", hol], capture_output=True, text=True)
    assert r.returncode != 0 and "holiday list ends" in (r.stderr + r.stdout), r.stderr
    # a prefecture name that disagrees with its code stops the build; no municipality at all gives empty names
    write_counters(full(code="13101"))
    r = subprocess.run(build[:3] + [os.path.join(tmp, "out8"), "--counters", counters], capture_output=True, text=True)
    assert r.returncode != 0 and "does not match" in (r.stderr + r.stdout), r.stderr
    write_counters(full(pref="", code=""))
    r = subprocess.run(build[:3] + [os.path.join(tmp, "out9"), "--counters", counters], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    d = pd.read_parquet(os.path.join(tmp, "out9", "hourly_2026-10_01-10.parquet"))
    assert d.prefecture.isna().all() and d.prefecture_en.isna().all()
    c9 = pd.read_csv(os.path.join(tmp, "out9", "counters.csv"), dtype=str, keep_default_na=False)
    assert (c9.prefecture_en == "").all() and (c9.municipality_en == "").all()
    # English names in the published counters.csv, next to the Japanese ones
    write_counters(full())
    r = subprocess.run(build[:3] + [os.path.join(tmp, "out11"), "--counters", counters], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    c11 = pd.read_csv(os.path.join(tmp, "out11", "counters.csv"), dtype=str, keep_default_na=False)
    assert list(c11.columns) == ["counter_id", "sensor", "road_type", "longitude", "latitude", "prefecture", "prefecture_en",
                                 "municipality_code", "municipality", "municipality_en", "town", "in_5min_box", "last_seen"]
    assert (c11.prefecture_en == "Kanagawa").all() and (c11.municipality_en == "Yokohama-shi Tsurumi-ku").all()
    assert (c11.municipality_code == "14101").all() and len(c11) == len(ids)
    assert c11.drop(columns=["prefecture_en", "municipality_en"]).equals(pd.read_csv(counters, dtype=str, keep_default_na=False))
    write_counters([[i, s, 3, 139.6, 35.4, "神奈川", "14101", "x", "", True, "2026-10-05"] for i, s in sorted(ids)])
    r = subprocess.run(build[:3] + [os.path.join(tmp, "out13"), "--counters", counters], capture_output=True, text=True)
    assert r.returncode != 0 and "does not match" in (r.stderr + r.stdout), r.stderr  # not one of the 47: places() stops first
    write_counters(full())
    names = os.path.join(tmp, "names.csv")
    with open(names, "w", encoding="utf-8") as g:
        g.write("municipality_code,prefecture,municipality,municipality_kana,municipality_en\n01100,北海道,札幌市,サッポロシ,Sapporo-shi\n")
    r = subprocess.run(build[:3] + [os.path.join(tmp, "out12"), "--counters", counters, "--names", names], capture_output=True, text=True)
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
