"""Offline tests of convert.py and verify.py on small synthetic Shift-JIS archives shaped like the real ones
(three header generations, a region archive with the same CSV twice, point 0, conflicting and repeated rows,
times outside the month, bad counts, a monthly archive that holds regional zips)."""
import gzip, io, json, os, shutil, sys, tempfile, zipfile
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import convert, verify

REAL_MIN = (convert.MIN_REGIONS, convert.MIN_POINTS)  # other tests lower these for tiny months

H10 = "時刻,情報源コード,計測地点番号,計測地点名称,2次メッシュコード,リンク区分,リンク番号,断面交通量,リンク終端からの距離（×10m）,リンクバージョン"
H9 = "時刻,情報源コード,計測地点番号,計測地点名称,2次メッシュコード,リンク区分,リンク番号,断面交通量,リンク終端からの距離(10m)"
H7 = "時刻,情報源コード,計測地点番号,計測地点名称,2次メッシュコード,リンク区分,断面交通量"


def row(h, t, src, pt, name, v, mesh="594031"):
    if h == H10:
        return f"{t},{src},{pt},{name},{mesh},2,150,{v},100,202500"
    if h == H9:
        return f"{t},{src},{pt},{name},{mesh},2,150,{v},100"
    return f"{t},{src},{pt},{name},{mesh},2,{v}"


def csv_bytes(h, rows):
    return ("\r\n".join([h] + rows) + "\r\n").encode("cp932")


def region_zip(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members:
            z.writestr(name, data)


def base_rows(h, src, fmt, year=2020, month=4, points=(1, 2), hours=3):
    out = []
    for pt in points:
        for k in range(hours * 12):
            hh, mm = divmod(k * 5, 60)
            t = fmt.format(y=year, m=month, d=1, H=hh, M=mm)
            out.append(row(h, t, src, pt, f"Ｒ１３上り　地点{pt}", (pt * 7 + k) % 50))
    return out


F_NEW = "{y}/{m:02d}/{d:02d} {H:02d}:{M:02d}"
F_OLD = "{y}/{m}/{d} {H}:{M:02d}:00"


def naive_hourly(rows_by_key):
    out = {}
    for (src, pt, d, h, mi), v in rows_by_key.items():
        s, n = out.get((src, pt, d, h), (0, 0))
        out[(src, pt, d, h)] = (s + (v or 0), n + (v is not None))
    return out


def run(tmp, ym, inputs, **kw):
    out, work = os.path.join(tmp, "out"), os.path.join(tmp, "work")
    shutil.rmtree(out, ignore_errors=True)
    return convert.convert_month(ym, inputs, out, work, **kw), out


def hourly(out, tag):
    return pl.read_parquet(os.path.join(out, f"police_hourly_{tag}.parquet"))


def test_three_generations_and_cleaning():
    convert.MIN_REGIONS, convert.MIN_POINTS = 1, 1
    with tempfile.TemporaryDirectory() as tmp:
        ins = []
        # generation 1 (2017-01): 7 columns, unpadded times with seconds
        a = base_rows(H7, "3017", F_OLD, points=(1, 2))
        region_zip(os.path.join(tmp, "typeB_toyama_2020_04.zip"), [("富山県警_202004.csv", csv_bytes(H7, a))])
        # generation 2: 9 columns; the same file twice (as Tochigi 2026-08); point 0 rows; a repeated row;
        # a conflicting row; a time outside the month; a time not on a 5-minute mark; a bad count; empty name
        b = base_rows(H9, "300E", F_NEW, points=(1, 2, 3))
        b += [row(H9, "2020/04/01 00:00", "300E", 0, "未定義", 9),
              row(H9, "2020/04/01 00:00", "300E", 1, "Ｒ１３上り　地点1", 7),          # same as the original row
              row(H9, "2020/04/01 00:05", "300E", 2, "Ｒ１３上り　地点2", 99),          # conflicts with 15
              row(H9, "2020/05/01 00:00", "300E", 1, "Ｒ１３上り　地点1", 5),
              row(H9, "2020/04/01 00:07", "300E", 1, "Ｒ１３上り　地点1", 5),
              row(H9, "2020/04/01 03:00", "300E", 3, "", "x")]                       # a new hour, bad count
        data = csv_bytes(H9, b)
        region_zip(os.path.join(tmp, "typeB_tochigi_2020_04.zip"),
                   [("typeB_tochigi_2020_04/", b""), ("typeB_tochigi_2020_04/栃木県警_.csv", data),
                    ("typeB_tochigi_2020_04/栃木県警_202004.csv", data)])
        # generation 3 inside a monthly archive (as the compusophia files)
        c = base_rows(H10, "3009", F_NEW, points=(1,))
        inner = io.BytesIO()
        region_zip(inner, [("typeB_akita_2020_04/秋田県警_202004.csv", csv_bytes(H10, c))])
        with zipfile.ZipFile(os.path.join(tmp, "2020_04.zip"), "w") as z:
            z.writestr("typeB_akita_2020_04.zip", inner.getvalue())
        ins = [os.path.join(tmp, f) for f in ("typeB_toyama_2020_04.zip", "typeB_tochigi_2020_04.zip", "2020_04.zip")]
        man, out = run(tmp, "2020-04", ins, check_gates=False)  # a tiny month: the share gates ring by design
        tg = man["regions"]["tochigi"]
        assert len(tg["identical_members_dropped"]) == 1, tg
        assert tg["point_zero_rows"] == 1, tg
        assert tg["outside_rows"] == 2, tg
        assert tg["same_value_duplicates"] == 1, tg
        assert tg["conflict_keys"] == 1 and tg["conflict_rows"] == 2, tg
        assert tg["bad_value_rows"] == 1, tg
        assert man["totals"]["regions"] == 3, man["totals"]
        # 2 of about 220 rows ring the 0.1% gates; 1 bad count stays inside the 1% gate
        assert man["gate_failures"] == ["outside rows 2", "conflicting rows 2"], man["gate_failures"]
        h = hourly(out, "202004")
        # naive recomputation of every hour from the rows written above
        exp = {}
        for src, rows, hd in (("3017", a, H7), ("300E", b, H9), ("3009", c, H10)):
            vals = {}
            for r in rows:
                f = r.split(",")
                t, pt, v = f[0], int(f[2]), f[7] if hd != H7 else f[6]
                if pt == 0:
                    continue
                d, hm = t.split(" ")
                Y, M, D = (int(x) for x in d.split("/"))
                hh, mi = int(hm.split(":")[0]), int(hm.split(":")[1])
                if (Y, M) != (2020, 4) or mi % 5:
                    continue
                vals.setdefault((src, pt, D, hh, mi), set()).add(int(v) if v.isdigit() else None)
            exp.update(naive_hourly({k: next(iter(s)) for k, s in vals.items() if len(s) == 1}))
        got = {(r["source"], r["point"], r["hour_jst"].day, r["hour_jst"].hour): (r["vehicles"] or 0, r["n_5min"])
               for r in h.iter_rows(named=True)}
        assert got == exp, (len(got), len(exp), sorted(set(got.items()) ^ set(exp.items()))[:5])
        # the hour holding the conflicting time has one value fewer; the bad count is missing, not zero
        assert got[("300E", 2, 1, 0)][1] == 11 and got[("300E", 3, 1, 3)] == (0, 0)
        assert h.filter(pl.col("source") == "300E", pl.col("point") == 3, pl.col("hour_jst").dt.hour() == 3)["vehicles"][0] is None
        pts = pl.read_csv(gzip.open(os.path.join(out, "police_points_202004.csv.gz")), infer_schema_length=0)
        assert pts.height == 6 and set(pts["region"]) == {"toyama", "tochigi", "akita"}, pts
        assert pts.filter(pl.col("source") == "300E", pl.col("point") == "3")["name"][0] == "Ｒ１３上り　地点3"
        five = pl.read_parquet(os.path.join(out, "police_5min_202004.parquet"))
        assert five.height == sum(r["rows_out"] for r in man["regions"].values())
        assert five.filter(pl.col("source") == "300E", pl.col("point") == 3, pl.col("time_jst").dt.hour() == 2,
                           pl.col("time_jst").dt.minute() == 55)["vehicles"][0] == 6
        assert five.filter(pl.col("source") == "300E", pl.col("point") == 3, pl.col("time_jst").dt.hour() == 3)["vehicles"][0] is None
        # the independent recomputation agrees on two regions
        chk = os.path.join(tmp, "chk")
        regs = verify.keep_two_smallest(ins, chk)
        assert sorted(regs) == ["akita", "toyama"], regs
        verify.check("2020-04", chk, regs, out)
        # and on the region that holds every cleaning case
        os.makedirs(chk)
        shutil.copyfile(os.path.join(tmp, "typeB_tochigi_2020_04.zip"), os.path.join(chk, "tochigi.zip"))
        verify.check("2020-04", chk, ["tochigi"], out)


def test_hour_with_only_bad_counts_is_missing():
    convert.MIN_REGIONS, convert.MIN_POINTS = 1, 1
    with tempfile.TemporaryDirectory() as tmp:
        rows = [row(H10, f"2021/02/28 23:{m:02d}", "3009", 5, "ｘ", "x") for m in range(0, 60, 5)]
        rows += [row(H10, "2021/02/28 22:00", "3009", 5, "ｘ", 3)]
        p = os.path.join(tmp, "typeB_akita_2021_02.zip")
        region_zip(p, [("a.csv", csv_bytes(H10, rows))])
        man, out = run(tmp, "2021-02", [p], check_gates=False)
        h = hourly(out, "202102").sort("hour_jst")
        assert h["n_5min"].to_list() == [1, 0] and h["vehicles"].to_list() == [3, None], h


def test_unknown_header_and_double_region_fail():
    convert.MIN_REGIONS, convert.MIN_POINTS = 1, 1
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "typeB_akita_2021_02.zip")
        region_zip(p, [("a.csv", csv_bytes(H10.replace("断面交通量", "交通量"), []))])
        try:
            run(tmp, "2021-02", [p])
        except ValueError as e:
            assert "unknown column" in str(e)
        else:
            raise AssertionError("unknown header accepted")
        q = os.path.join(tmp, "x", "typeB_akita_2021_02.zip")
        os.makedirs(os.path.dirname(q))
        region_zip(q, [("a.csv", csv_bytes(H10, base_rows(H10, "3009", F_NEW, year=2021, month=2)))])
        region_zip(p, [("a.csv", csv_bytes(H10, base_rows(H10, "3009", F_NEW, year=2021, month=2)))])
        try:
            run(tmp, "2021-02", [p, q])
        except ValueError as e:
            assert "twice" in str(e)
        else:
            raise AssertionError("a region given twice was accepted")


def test_gates():
    convert.MIN_REGIONS, convert.MIN_POINTS = REAL_MIN
    assert REAL_MIN == (30, 5000), REAL_MIN
    base = {"rows_in": 100000, "point_zero_rows": 0, "outside_rows": 0, "conflict_rows": 0, "bad_value_rows": 0,
            "regions": 51, "points": 38000}
    assert convert.gate_failures(dict(base)) == []
    for k, v in (("outside_rows", 101), ("conflict_rows", 101), ("bad_value_rows", 1001), ("regions", 29),
                 ("points", 4999)):
        assert convert.gate_failures({**base, k: v}), k
    for k, v in (("outside_rows", 100), ("conflict_rows", 100), ("bad_value_rows", 1000), ("regions", 30),
                 ("points", 5000)):
        assert not convert.gate_failures({**base, k: v}), k


def test_verify_catches_a_wrong_hour():
    convert.MIN_REGIONS, convert.MIN_POINTS = 1, 1
    with tempfile.TemporaryDirectory() as tmp:
        ps = []
        for r, src in (("akita", "3009"), ("toyama", "3017")):
            p = os.path.join(tmp, f"typeB_{r}_2022_03.zip")
            region_zip(p, [("a.csv", csv_bytes(H10, base_rows(H10, src, F_NEW, year=2022, month=3)))])
            ps.append(p)
        man, out = run(tmp, "2022-03", ps)
        hp = os.path.join(out, "police_hourly_202203.parquet")
        h = pl.read_parquet(hp)
        h.with_columns(pl.when(pl.int_range(pl.len()) == 3).then(pl.col("vehicles") + 1)
                       .otherwise(pl.col("vehicles")).alias("vehicles")).write_parquet(hp)
        chk = os.path.join(tmp, "chk")
        regs = verify.keep_two_smallest(ps, chk)
        try:
            verify.check("2022-03", chk, regs, out)
        except SystemExit as e:
            assert "differ" in str(e)
        else:
            raise AssertionError("verify accepted a changed hour")


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f()
            print("ok", name)
