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
    h = pd.read_parquet(os.path.join(out, "hourly_2026-10.parquet"))
    m = pd.read_parquet(os.path.join(out, "five_minute_kanto_2026-10.parquet"))
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
    assert sorted(os.listdir(out)) == ["counters.csv", "five_minute_kanto_2026-10.parquet", "hourly_2026-10.parquet"]
    # 3. Kaggle metadata: one resource per file, every column described; an undescribed column stops it
    meta = [sys.executable, os.path.join(HERE, "make_kaggle_meta.py")]
    settings = os.path.join(tmp, "settings.json")
    r = subprocess.run(meta + [out, "--settings", settings], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    md = json.load(open(os.path.join(out, "dataset-metadata.json"), encoding="utf-8"))
    FILES = ["counters.csv", "five_minute_kanto_2026-10.parquet", "hourly_2026-10.parquet"]
    assert sorted(x["path"] for x in md["resources"]) == FILES
    for x in md["resources"]:
        names = [f["name"] for f in x["schema"]["fields"]]
        actual = list(pd.read_csv(os.path.join(out, x["path"]), nrows=0).columns) if x["path"].endswith(".csv")             else list(pd.read_parquet(os.path.join(out, x["path"])).columns)
        assert names == actual and all(f["description"] for f in x["schema"]["fields"]), x["path"]
    st = json.load(open(settings, encoding="utf-8"))
    assert set(st["files"]) == {x["path"] for x in md["resources"]}
    assert md["licenses"] == [{"name": "CC-BY-4.0"}] and len(md["keywords"]) <= 5
    h.assign(extra=1).to_parquet(os.path.join(out, "hourly_2026-10.parquet"), index=False)
    r = subprocess.run(meta + [out, "--settings", settings], capture_output=True, text=True)
    assert r.returncode != 0 and "without a description" in (r.stderr + r.stdout), r.stderr
    # 4. a counter missing from counters.csv fails the build
    with open(counters, "w", newline="", encoding="utf-8") as g:
        w = csv.writer(g); w.writerow(["counter_id", "sensor"]); w.writerows(sorted(ids)[1:])  # build_kaggle reads only these two
    r = subprocess.run(build[:3] + [out + "2", "--counters", counters], capture_output=True, text=True)
    assert r.returncode != 0 and "not in" in (r.stderr + r.stdout), r.stderr
print("test_fixtures: ALL PASS")
