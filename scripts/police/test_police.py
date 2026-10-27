"""Offline tests of convert.py, verify.py and police.py on small synthetic Shift-JIS archives shaped like the real
ones (three header generations, a region archive with the same CSV twice, point 0, conflicting and repeated rows,
times outside the month, bad counts, a monthly archive that holds regional zips, an lzh archive named .zip as
2019-06 Hyogo), and of police.py's bookkeeping against a fake release."""
import gzip, io, json, os, shutil, struct, subprocess, sys, tempfile, types, zipfile
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import convert, verify, police

REAL = (convert.MIN_POINTS, convert.REGIONS)  # other tests shrink these for tiny months

H10 = "時刻,情報源コード,計測地点番号,計測地点名称,2次メッシュコード,リンク区分,リンク番号,断面交通量,リンク終端からの距離（×10m）,リンクバージョン"
H9 = "時刻,情報源コード,計測地点番号,計測地点名称,2次メッシュコード,リンク区分,リンク番号,断面交通量,リンク終端からの距離(10m)"
H7 = "時刻,情報源コード,計測地点番号,計測地点名称,2次メッシュコード,リンク区分,断面交通量"


def tiny():
    convert.MIN_POINTS = 1


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


# --- a minimal LHA writer (stored, -lh0-), level 0 or level 2 headers as in the real 2019-06 Hyogo file
def crc16(data, crc=0):
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def lzh_member(name, data, level=2, method=b"-lh0-", crc=None):
    nb = name.encode("cp932") if isinstance(name, str) else name
    c = crc16(data) if crc is None else crc
    if level == 0:
        body = method + struct.pack("<IIIBB", len(data), len(data), 0x5A210000, 0x20, 0) + bytes([len(nb)]) + nb \
            + struct.pack("<H", c)
        return bytes([len(body), sum(body) & 0xFF]) + body + data
    if level == 1:  # a placeholder name in the base header, the real one in an extended header
        ext = b"\x01" + nb
        esize = len(ext) + 2
        body = method + struct.pack("<IIIBB", len(data) + esize, len(data), 0x5A210000, 0x20, 1) + b"\x01x" \
            + struct.pack("<H", c) + b"M" + struct.pack("<H", esize)
        return bytes([len(body), sum(body) & 0xFF]) + body + ext + struct.pack("<H", 0) + data
    exts = [b"\x00" + b"\x00\x00", b"\x01" + nb]  # common header (header crc), file name
    sizes = [len(e) + 2 for e in exts]
    ext_bytes = b"".join(e + struct.pack("<H", sizes[i + 1] if i + 1 < len(exts) else 0) for i, e in enumerate(exts))
    hsize = 26 + len(ext_bytes)
    head = struct.pack("<H", hsize) + method + struct.pack("<IIIBB", len(data), len(data), 1560000000, 0x20, 2) \
        + struct.pack("<H", c) + b"M" + struct.pack("<H", sizes[0]) + ext_bytes
    hc = crc16(head)
    head = head[:27] + struct.pack("<H", hc) + head[29:]
    return head + data


def lzh(path, members, level=2):
    with open(path, "wb") as f:
        f.write(b"".join(lzh_member(n, d, level) if isinstance(d, bytes) else d for n, d in members) + b"\x00")


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


def points_of(out, tag):
    return pl.read_csv(gzip.open(os.path.join(out, f"police_points_{tag}.csv.gz")), infer_schema_length=0)


def verify_all(ym, ins, out, tmp, expect=None):
    chk = os.path.join(tmp, "chk")
    shutil.rmtree(chk, ignore_errors=True)
    regs, every = verify.pick(ins, chk, ym)
    if expect is not None:
        assert regs == expect, regs
    verify.check(ym, chk, regs, out, every)
    return regs


def raises(f, exc, text):
    try:
        f()
    except exc as e:
        assert text in str(e), (text, str(e))
    else:
        raise AssertionError(f"no {exc.__name__} with {text!r}")


def test_three_generations_and_cleaning():
    tiny()
    with tempfile.TemporaryDirectory() as tmp:
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
        # 2 of 113 Tochigi rows ring the 0.1% gates; 1 bad count stays inside the 1% gate
        assert [f for f in man["gate_failures"] if not f.startswith("regions missing")] == \
            ["tochigi outside_rows 2", "tochigi conflict_rows 2"], man["gate_failures"]
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
        pts = points_of(out, "202004")
        assert pts.height == 6 and set(pts["region"]) == {"toyama", "tochigi", "akita"}, pts
        assert pts.filter(pl.col("source") == "300E", pl.col("point") == "3")["name"][0] == "Ｒ１３上り　地点3"
        # rows: every row at a valid time, repeated and conflicting ones included (36 + 1 repeat; 36 + 1 conflict)
        assert pts.filter(pl.col("source") == "300E")["rows"].to_list() == ["37", "37", "37"], pts
        five = pl.read_parquet(os.path.join(out, "police_5min_202004.parquet"))
        assert five.height == sum(r["rows_out"] for r in man["regions"].values())
        assert five.filter(pl.col("source") == "300E", pl.col("point") == 3, pl.col("time_jst").dt.hour() == 2,
                           pl.col("time_jst").dt.minute() == 55)["vehicles"][0] == 6
        assert five.filter(pl.col("source") == "300E", pl.col("point") == 3, pl.col("time_jst").dt.hour() == 3)["vehicles"][0] is None
        for src in ("3017", "300E", "3009"):  # written region by region, each sorted by source, point and time
            sub = five.filter(pl.col("source") == src)
            assert sub.height and sub.sort("point", "time_jst").equals(sub), f"5-minute rows of {src} are not sorted"
        # the independent recomputation agrees: the two smallest and one more, here every region
        regs = verify_all("2020-04", ins, out, tmp)
        assert regs == ["akita", "tochigi", "toyama"], regs
        chk = os.path.join(tmp, "chk")
        os.makedirs(chk)
        shutil.copyfile(os.path.join(tmp, "typeB_tochigi_2020_04.zip"), os.path.join(chk, "tochigi.arc"))
        verify.check("2020-04", chk, ["tochigi"], out, ["akita", "tochigi", "toyama"])


def test_more_cleaning_and_points():
    """Negative and too large counts, seconds, the day before the month, non-integer points, the mode of a mesh."""
    tiny()
    with tempfile.TemporaryDirectory() as tmp:
        r = base_rows(H10, "3009", F_NEW, year=2021, month=3, points=(4, 5))
        r += [row(H10, "2021/03/01 03:00", "3009", 4, "Ｒ１３上り　地点4", "-3"),
              row(H10, "2021/03/01 03:05", "3009", 4, "Ｒ１３上り　地点4", "40000"),
              row(H10, "2021/03/01 03:10", "3009", 4, "Ｒ１３上り　地点4", "32767"),
              row(H10, "2021/03/01 01:05:30", "3009", 5, "Ｒ１３上り　地点5", "8"),
              row(H10, "2021/02/28 23:55", "3009", 5, "Ｒ１３上り　地点5", "8"),
              row(H10, "2021/03/01 03:00", "3009", "A1", "ｘ", "8"),
              row(H10, "2021/03/01 03:15", "3009", 5, "Ｒ１３上り　地点5", "1", mesh="999999"),
              row(H10, "2021/03/01 03:20", "3009", 5, "別名", "1", mesh="999999"),
              row(H10, "2021/03/01 24:00", "3009", 5, "Ｒ１３上り　地点5", "1"),        # not a time
              row(H10, "2021/03/01 00:00", "", 6, "情報源なし", "4")]                   # an empty source code
        p = os.path.join(tmp, "typeB_akita_2021_03.zip")
        region_zip(p, [("a.csv", csv_bytes(H10, r))])
        q = os.path.join(tmp, "typeB_toyama_2021_03.zip")
        region_zip(q, [("b.csv", csv_bytes(H10, base_rows(H10, "3017", F_NEW, year=2021, month=3)))])
        man, out = run(tmp, "2021-03", [p, q], check_gates=False)
        s = man["regions"]["akita"]
        assert s["bad_value_rows"] == 2 and s["outside_rows"] == 3 and s["point_not_integer"] == 1, s
        assert man["totals"]["point_not_integer"] == 1, man["totals"]
        f5 = pl.read_parquet(os.path.join(out, "police_5min_202103.parquet")).filter(pl.col("point") == 4,
                                                                                       pl.col("time_jst").dt.hour() == 3)
        assert f5.sort("time_jst")["vehicles"].to_list() == [None, None, 32767], f5
        pts = points_of(out, "202103").filter(pl.col("region") == "akita").sort("point")
        p5 = pts.filter(pl.col("point") == "5").row(0, named=True)
        assert p5["mesh2"] == "594031" and p5["n_mesh2"] == "2" and p5["n_names"] == "2", p5
        assert p5["name"] == "Ｒ１３上り　地点5" and p5["rows"] == "38", p5
        assert p5["first_jst"].startswith("2021-03-01T00:00") and p5["last_jst"].startswith("2021-03-01T03:20"), p5
        # the point with an empty source code keeps its attributes
        p6 = pts.filter(pl.col("point") == "6").row(0, named=True)
        assert p6["source"] in (None, "") and p6["name"] == "情報源なし" and p6["n_names"] == "1", p6
        assert pts["point"].to_list() == ["4", "5", "6"], pts  # (sorted by point above)
        verify_all("2021-03", [p, q], out, tmp, expect=["akita", "toyama"])


def test_parsing_parity():
    """Inputs where a regex and an integer cast could disagree: a sign, full-width digits, a point beyond Int32,
    one-digit minutes, a short row. The converter and the independent check must read them alike."""
    tiny()
    with tempfile.TemporaryDirectory() as tmp:
        r = base_rows(H10, "3009", F_NEW, year=2021, month=5, points=(1,))
        r += [row(H10, "2021/05/02 00:00", "3009", 1, "ｘ", "+5"),
              row(H10, "2021/05/02 00:05", "3009", 1, "ｘ", "５"),
              row(H10, "2021/05/02 00:10", "3009", "+3", "ｙ", "7"),
              row(H10, "2021/05/02 00:15", "3009", "2147483648", "ｚ", "7"),
              row(H10, "2021/05/02 0:5", "3009", 2, "ｗ", "9"),
              "2021/05/02 00:20,3009,1,ｘ,594031,2,150,6,100"]  # short: no link_version
        p = os.path.join(tmp, "typeB_akita_2021_05.zip")
        region_zip(p, [("a.csv", csv_bytes(H10, r))])
        q = os.path.join(tmp, "typeB_toyama_2021_05.zip")
        region_zip(q, [("b.csv", csv_bytes(H10, base_rows(H10, "3017", F_NEW, year=2021, month=5)))])
        man, out = run(tmp, "2021-05", [p, q], check_gates=False)
        five = pl.read_parquet(os.path.join(out, "police_5min_202105.parquet")).filter(pl.col("time_jst").dt.day() == 2)
        got = {(x["point"], x["time_jst"].minute): x["vehicles"] for x in five.iter_rows(named=True)}
        assert got == {(1, 0): 5, (1, 5): None, (3, 10): 7, (2, 5): 9, (1, 20): 6}, got
        assert man["regions"]["akita"]["point_not_integer"] == 1, man["regions"]["akita"]
        verify_all("2021-05", [p, q], out, tmp, expect=["akita", "toyama"])


def test_lzh_region():
    """A regional archive that is an lzh under a .zip name (2019-06 Hyogo), level 0, 1 and 2 headers."""
    tiny()
    for level in (0, 1, 2):
        with tempfile.TemporaryDirectory() as tmp:
            rows = base_rows(H10, "3020", F_NEW, year=2019, month=6, points=(1, 2, 3))
            p = os.path.join(tmp, "typeB_hyogo_2019_06.zip")
            lzh(p, [("兵庫県警_201906.csv", csv_bytes(H10, rows))], level=level)
            assert [m[0] for m in convert.lzh_members(p)] == ["兵庫県警_201906.csv"]
            q = os.path.join(tmp, "typeB_akita_2019_06.zip")
            region_zip(q, [("a.csv", csv_bytes(H10, base_rows(H10, "3009", F_NEW, year=2019, month=6)))])
            r = os.path.join(tmp, "typeB_toyama_2019_06.zip")
            region_zip(r, [("b.csv", csv_bytes(H10, base_rows(H10, "3017", F_NEW, year=2019, month=6)))])
            man, out = run(tmp, "2019-06", [p, q, r], check_gates=False)
            assert man["regions"]["hyogo"]["archive"] == "lzh" and man["regions"]["hyogo"]["rows_out"] == 108, man
            assert man["regions"]["hyogo"]["members"] == ["兵庫県警_201906.csv"], man["regions"]["hyogo"]
            regs = verify_all("2019-06", [p, q, r], out, tmp)
            assert "hyogo" in regs, regs


def test_nested_region_zip():
    """A regional zip whose only file is the same regional zip one folder down (2023-06 Akita) is read through,
    by the converter and by the independent check; anything else wrapped that way still stops the month."""
    tiny()
    with tempfile.TemporaryDirectory() as tmp:
        rows = base_rows(H10, "3009", F_NEW, year=2023, month=6, points=(1, 2, 3))
        inner = io.BytesIO()
        region_zip(inner, [("a.csv", csv_bytes(H10, rows))])
        p = os.path.join(tmp, "typeB_akita_2023_06.zip")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("typeB_akita_2023_06/", b"")
            z.writestr("typeB_akita_2023_06/typeB_akita_2023_06.zip", inner.getvalue())
        q = os.path.join(tmp, "typeB_toyama_2023_06.zip")
        region_zip(q, [("b.csv", csv_bytes(H10, base_rows(H10, "3017", F_NEW, year=2023, month=6)))])
        man, out = run(tmp, "2023-06", [p, q], check_gates=False)
        assert man["regions"]["akita"]["rows_out"] == 108 and man["regions"]["akita"]["members"] == ["a.csv"], man
        regs = verify_all("2023-06", [p, q], out, tmp)
        assert "akita" in regs, regs
        # a zip of another region (or month) inside, or a second file next to it, is not unwrapped
        for wrapped in (["typeB_akita_2023_06/typeB_aomori_2023_06.zip"],
                        ["typeB_akita_2023_06/typeB_akita_2023_06.zip", "readme.txt"]):
            with zipfile.ZipFile(p, "w") as z:
                for n in wrapped:
                    z.writestr(n, inner.getvalue())
            raises(lambda: run(tmp, "2023-06", [p, q], check_gates=False), ValueError, "unexpected member")


def test_lzh_refused():
    tiny()
    with tempfile.TemporaryDirectory() as tmp:
        good = csv_bytes(H10, base_rows(H10, "3020", F_NEW, year=2019, month=6))
        cases = [
            ("not a csv", [("readme.txt", b"hello")], "unexpected member"),
            ("a link", [("x|a.csv", good)], "unexpected member"),  # Unix LHA stores a link as name|target
        ]
        for what, members, text in cases:
            p = os.path.join(tmp, "typeB_hyogo_2019_06.zip")
            lzh(p, members)
            raises(lambda: convert.extract_csvs(p, os.path.join(tmp, "w")), ValueError, text)
        # a stored member whose bytes do not match its CRC: lha prints it anyway; the test command catches it
        p = os.path.join(tmp, "typeB_hyogo_2019_06.zip")
        with open(p, "wb") as f:
            f.write(lzh_member("a.csv", good, crc=crc16(good) ^ 1) + b"\x00")
        raises(lambda: convert.extract_csvs(p, os.path.join(tmp, "w")), ValueError, "lha test failed")
        # cut short: the header says more bytes than the file has
        with open(p, "wb") as f:
            f.write(lzh_member("a.csv", good)[:-10])
        raises(lambda: convert.extract_csvs(p, os.path.join(tmp, "w")), ValueError, "runs past the end")
        # lha prints fewer bytes than the headers promise
        real = convert.lha
        def short(args, cwd, stdout=None):
            r = real(args, cwd, stdout)
            if args[0] == "pq":
                stdout.truncate(stdout.tell() - 1)
            return r
        lzh(p, [("a.csv", good)])
        convert.lha = short
        try:
            raises(lambda: convert.extract_csvs(p, os.path.join(tmp, "w")), ValueError, "lha printed")
            # and without that check, the split still stops at the end of the output instead of looping
            saved = convert.os.path.getsize
            convert.os.path.getsize = lambda q: sum(o for _, _, o in convert.lzh_members(p)) if q.endswith("lzh.all") \
                else saved(q)
            try:
                raises(lambda: convert.extract_csvs(p, os.path.join(tmp, "w")), ValueError, "ends inside")
            finally:
                convert.os.path.getsize = saved
        finally:
            convert.lha = real
        # not an archive at all
        with open(p, "wb") as f:
            f.write(b"broken")
        raises(lambda: convert.extract_csvs(p, os.path.join(tmp, "w")), zipfile.BadZipFile, "")
        with open(p, "wb") as f:
            f.write(b"\x40\x00-lhX-" + b"\x00" * 40)
        raises(lambda: convert.lzh_members(p), ValueError, "not an LHA header")
        # a level-0 header whose size byte is too small for its own fields, or larger than the file
        m0 = bytearray(lzh_member("a.csv", good, level=0))
        for size, keep in ((18, None), (21, None), (200, 40)):  # the last one: a 40-byte file
            m = bytearray(m0)
            m[0] = size
            open(p, "wb").write((bytes(m) + b"\x00")[:keep])
            raises(lambda: convert.lzh_members(p), ValueError, "bad level-0 header")
        # a level-1 header three bytes short: it holds the name and CRC but not the OS byte and next size
        m = bytearray(lzh_member("a.csv", good, level=1))
        m[0] -= 3
        open(p, "wb").write(bytes(m) + b"\x00")
        raises(lambda: convert.lzh_members(p), ValueError, "bad level-1 header")


def test_unexpected_members_and_double_region():
    tiny()
    with tempfile.TemporaryDirectory() as tmp:
        good = csv_bytes(H10, base_rows(H10, "3009", F_NEW, year=2021, month=2))
        inner = io.BytesIO()
        region_zip(inner, [("a.csv", good)])
        m = os.path.join(tmp, "2021_02.zip")
        with zipfile.ZipFile(m, "w") as z:
            z.writestr("typeB_akita_2021_02.zip", inner.getvalue())
            z.writestr("readme.txt", b"x")
        raises(lambda: run(tmp, "2021-02", [m]), ValueError, "unexpected member")
        raises(lambda: verify.regional_members([m], 2021, 2), SystemExit, "unexpected member")
        with zipfile.ZipFile(m, "w") as z:
            z.writestr("typeB_akita_2021_03.zip", inner.getvalue())  # another month
        raises(lambda: run(tmp, "2021-02", [m]), ValueError, "unexpected member")
        p = os.path.join(tmp, "typeB_akita_2021_02.zip")
        region_zip(p, [("a.csv", good), ("note.pdf", b"%PDF")])
        raises(lambda: run(tmp, "2021-02", [p]), ValueError, "unexpected member")
        region_zip(p, [("a.csv", csv_bytes(H10.replace("断面交通量", "交通量"), []))])
        raises(lambda: run(tmp, "2021-02", [p]), ValueError, "unknown column")
        q = os.path.join(tmp, "x", "typeB_akita_2021_02.zip")
        os.makedirs(os.path.dirname(q))
        region_zip(q, [("a.csv", good)])
        region_zip(p, [("a.csv", good)])
        raises(lambda: run(tmp, "2021-02", [p, q]), ValueError, "twice")


def test_hour_with_only_bad_counts_is_missing():
    tiny()
    with tempfile.TemporaryDirectory() as tmp:
        rows = [row(H10, f"2021/02/28 23:{m:02d}", "3009", 5, "ｘ", "x") for m in range(0, 60, 5)]
        rows += [row(H10, "2021/02/28 22:00", "3009", 5, "ｘ", 3)]
        p = os.path.join(tmp, "typeB_akita_2021_02.zip")
        region_zip(p, [("a.csv", csv_bytes(H10, rows))])
        man, out = run(tmp, "2021-02", [p], check_gates=False)
        h = hourly(out, "202102").sort("hour_jst")
        assert h["n_5min"].to_list() == [1, 0] and h["vehicles"].to_list() == [3, None], h


def test_gates():
    convert.MIN_POINTS, convert.REGIONS = REAL
    assert convert.MIN_POINTS == 5000 and len(convert.REGIONS) == 51
    st = {"rows_in": 100000, "point_zero_rows": 0, "outside_rows": 0, "conflict_rows": 0, "bad_value_rows": 0,
          "point_not_integer": 0, "rows_out": 100000}
    regions = {r: dict(st) for r in convert.REGIONS}
    tot = {"points": 38000}
    assert convert.gate_failures(2026, 8, regions, tot) == []
    for k, bad, ok in (("outside_rows", 101, 100), ("conflict_rows", 101, 100), ("bad_value_rows", 1001, 1000),
                       ("point_not_integer", 101, 100)):
        assert convert.gate_failures(2026, 8, {**regions, "akita": {**st, k: bad}}, tot) == [f"akita {k} {bad}"], k
        assert convert.gate_failures(2026, 8, {**regions, "akita": {**st, k: ok}}, tot) == [], k
    # the share is of the rows with a usable point: point 0 rows do not dilute it
    assert convert.gate_failures(2026, 8, {**regions, "akita": {**st, "point_zero_rows": 50000, "outside_rows": 51}},
                                 tot) == ["akita outside_rows 51"]
    assert convert.gate_failures(2026, 8, {**regions, "akita": {**st, "rows_out": 0}}, tot) == ["akita no rows"]
    assert convert.gate_failures(2026, 8, regions, {"points": 4999}) == ["points 4999"]
    assert convert.gate_failures(2026, 8, regions, {"points": 5000}) == []
    # from 2017-08 every region must be there; before, exactly the regions the archive holds for that month
    less = {r: dict(st) for r in sorted(convert.REGIONS)[:-1]}
    assert convert.gate_failures(2017, 8, less, tot) == [f"regions missing ['{sorted(convert.REGIONS)[-1]}']"]
    sizes = {}
    for (y, m), miss in convert.EARLY_MISSING.items():
        early = {r: dict(st) for r in convert.REGIONS - miss}
        sizes[m] = len(early)
        assert convert.gate_failures(y, m, early, tot) == [], (y, m)
        gone = sorted(early)[5]
        early.pop(gone)
        assert convert.gate_failures(y, m, early, tot) == [f"regions missing ['{gone}']"], (y, m)
    assert sizes == {1: 39, 2: 38, 3: 38, 4: 38, 5: 42, 6: 42, 7: 42}, sizes  # as counted in the archive
    # and the regions themselves, as read from the archive's central directories (survey of 2026-10-17)
    m9 = {"ehime", "fukuoka", "gifu", "hiroshima", "hyogo", "kagawa", "okayama", "okinawa", "tokushima"}
    m13 = m9 | {"akita", "saitama", "shimane", "tottori"}
    assert convert.EARLY_MISSING == {(2017, 1): m13 - {"hiroshima"}, (2017, 2): m13, (2017, 3): m13, (2017, 4): m13,
                                     (2017, 5): m9, (2017, 6): m9, (2017, 7): m9}, convert.EARLY_MISSING
    assert convert.gate_failures(2016, 12, regions, tot) == ["no region list for 2016-12"]
    assert convert.gate_failures(2017, 7, {**regions, "atlantis": dict(st)}, tot) == ["unknown regions ['atlantis']"]


def test_verify_catches_changes():
    tiny()
    with tempfile.TemporaryDirectory() as tmp:
        ps = []
        for r, src in (("akita", "3009"), ("toyama", "3017")):
            p = os.path.join(tmp, f"typeB_{r}_2022_03.zip")
            region_zip(p, [("a.csv", csv_bytes(H10, base_rows(H10, src, F_NEW, year=2022, month=3)))])
            ps.append(p)
        man, out = run(tmp, "2022-03", ps, check_gates=False)
        hp = os.path.join(out, "police_hourly_202203.parquet")
        fp = os.path.join(out, "police_5min_202203.parquet")
        pp = os.path.join(out, "police_points_202203.csv.gz")
        mp = os.path.join(out, "police_manifest_202203.json")
        keep = {x: open(x, "rb").read() for x in (hp, fp, pp, mp)}

        def changed(path, f, text):
            f()
            raises(lambda: verify_all("2022-03", ps, out, tmp), SystemExit, text)
            for x, data in keep.items():
                open(x, "wb").write(data)

        nth = pl.int_range(pl.len()) == 3
        h, five = pl.read_parquet(hp), pl.read_parquet(fp)
        changed(hp, lambda: h.with_columns(pl.when(nth).then(pl.col("vehicles") + 1).otherwise(pl.col("vehicles"))
                                           ).write_parquet(hp), "hourly values differ")
        changed(hp, lambda: h.with_columns(pl.when(nth).then(pl.col("hour_jst") + pl.duration(days=9))
                                           .otherwise(pl.col("hour_jst"))).write_parquet(hp), "hourly keys differ")
        changed(hp, lambda: h.filter(~nth).write_parquet(hp), "hourly keys differ")
        changed(hp, lambda: pl.concat([h, h.head(1)]).write_parquet(hp), "written twice")
        changed(fp, lambda: five.with_columns(pl.when(nth).then(pl.col("vehicles") + 1).otherwise(pl.col("vehicles"))
                                              ).write_parquet(fp), "5-minute values differ")
        changed(fp, lambda: five.with_columns(pl.when(nth).then(pl.col("time_jst") + pl.duration(days=9))
                                              .otherwise(pl.col("time_jst"))).write_parquet(fp), "5-minute keys differ")
        changed(fp, lambda: pl.concat([five, five.head(1)]).write_parquet(fp), "written twice")
        changed(fp, lambda: five.filter(~nth).write_parquet(fp), "5-minute keys differ")  # a row lost
        changed(fp, lambda: pl.concat([five, five.head(1).with_columns(pl.col("time_jst") + pl.duration(days=20))])
                .write_parquet(fp), "5-minute keys differ")  # a row more
        pts = points_of(out, "202203")

        def drop_point():
            with gzip.open(pp, "wb") as f:
                pts.filter(pl.int_range(pl.len()) != 0).write_csv(f)
        changed(pp, drop_point, "point list differs")
        for col, val in (("name", "別の名前"), ("mesh2", "000000"), ("link_no", "9"), ("rows", "1"),
                         ("first_jst", "2022-03-01T00:05:00.000"), ("last_jst", "2022-03-01T00:00:00.000"),
                         ("n_names", "2"), ("n_mesh2", "0")):
            def change_attr():
                with gzip.open(pp, "wb") as f:
                    pts.with_columns(pl.when(pl.int_range(pl.len()) == 1).then(pl.lit(val)).otherwise(pl.col(col))
                                     .alias(col)).write_csv(f)
            changed(pp, change_attr, "point attributes differ")

        def drop_region():
            m = json.load(open(mp, encoding="utf-8"))
            m["regions"].pop("toyama")
            json.dump(m, open(mp, "w", encoding="utf-8"))
        changed(mp, drop_region, "manifest regions")
        verify_all("2022-03", ps, out, tmp)  # and the untouched files pass


# --- police.py against a fake release -----------------------------------------------------------------------------

class FakeGitHub:
    def __init__(self, releases=None):
        self.rel = releases or {}  # tag -> {name: {"id", "state", "size"}}
        self.calls, self.next_id = [], 1000

    def __call__(self, *args):
        self.calls.append(args)
        ok = lambda out="": types.SimpleNamespace(returncode=0, stdout=out, stderr="")
        if args[0] == "api" and args[1].startswith("repos/") and "/releases/tags/" in args[1]:
            tag = args[1].rsplit("/", 1)[1]
            if tag not in self.rel:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="HTTP 404: Not Found")
            return ok(tag)
        if args[:2] == ("api", "--paginate"):
            tag = args[2].split("/releases/")[1].split("/")[0]
            return ok("".join(f"{n}\t{a['id']}\t{a['state']}\t{a['size']}\n" for n, a in self.rel[tag].items()))
        if args[:2] == ("release", "create"):
            self.rel.setdefault(args[2], {})
            return ok()
        if args[:3] == ("api", "-X", "DELETE"):
            aid = args[3].rsplit("/", 1)[1]
            for assets in self.rel.values():
                for n in [n for n, a in assets.items() if a["id"] == aid]:
                    del assets[n]
            return ok()
        if args[:2] == ("release", "upload"):
            assert "--clobber" in args
            tag, path = args[2], args[3]
            self.next_id += 1
            self.rel[tag][os.path.basename(path)] = {"id": str(self.next_id), "state": "uploaded",
                                                     "size": os.path.getsize(path)}
            return ok()
        raise AssertionError(args)


def full_month(t):
    return {n.format(t=t): {"id": f"{n}{t}", "state": "uploaded", "size": 10} for n in police.KINDS}


def drive(gh, argv, official=(2026, 8), fail=None, download_fail=None, listing=None, unavailable=None):
    """Run police.main with the network and the converter replaced; returns (exit code, output, months converted)."""
    done, lines = [], []
    police.collect.gh = gh
    police.official_listing = listing or (lambda: (official, ["/x/typeB_akita_2026_08.zip"]))

    # the real fetch_inputs, with HEAD and the transfer replaced
    def available(url):
        if unavailable and f"{unavailable[0]:04d}_{unavailable[1]:02d}" in url:
            raise RuntimeError(f"not available (22): {url}")

    def curl(url, path):
        if download_fail and f"{download_fail[0]:04d}_{download_fail[1]:02d}" in url:
            raise RuntimeError("download failed (56)")
        open(path, "wb").write(b"x")
    police.available, police.curl = available, curl

    def convert_and_check(ym, paths, sources, work):
        done.append(ym)
        if fail and ym == fail:
            raise SystemExit(f"{ym}: gates failed: ['akita no rows']")
        out = os.path.join(work, "out")
        os.makedirs(out, exist_ok=True)
        y, m = int(ym[:4]), int(ym[5:7])
        for n in police.names_for(y, m):
            open(os.path.join(out, n), "w").write(n)
        return out
    police.convert_and_check = convert_and_check
    import builtins
    real = builtins.print
    builtins.print = lambda *a, **k: lines.append(" ".join(map(str, a)))
    with tempfile.TemporaryDirectory() as tmp:
        sys.argv = ["police.py", "--work", tmp, *argv]
        try:
            police.main(); code = 0
        except SystemExit as e:
            code = e.code if e.code is not None else 0
        except RuntimeError as e:
            code = str(e)
        finally:
            builtins.print = real
    return code, "\n".join(lines), done


def test_police_order_completeness_and_failures():
    saved = (police.collect.gh, police.official_listing, police.available, police.curl, police.convert_and_check,
             police.SKIP_FILE, police.RETRY_FILE)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            police.SKIP_FILE = os.path.join(tmp, "skip.txt")
            police.RETRY_FILE = os.path.join(tmp, "retry.txt")
            open(police.SKIP_FILE, "w").write("# comment\n2026-05 the source file is broken\n")
            rel = {"police-2026": {**full_month("202606"), **full_month("202603")}}
            rel["police-2026"]["police_hourly_202603.parquet"]["state"] = "starter"  # an unfinished upload
            gh = FakeGitHub(rel)
            code, out, done = drive(gh, ["--dry-run"])
            assert code == 0 and done == [], out
            # the official month first, then the newest back; 2026-06 complete; 2026-05 skipped; 2026-03 unfinished
            assert "next: ['2026-08', '2026-07', '2026-04', '2026-03', '2026-02', '2026-01']" in out, out
            assert "skip 2026-05: the source file is broken" in out
            # a stale partial month: its old files go before the new ones arrive
            code, out, done = drive(gh, ["--month", "2026-03"])
            assert code == 0 and done == ["2026-03"], (code, out)
            assert all(a["state"] == "uploaded" and a["id"].isdigit() for n, a in gh.rel["police-2026"].items()
                       if n.endswith("202603.parquet") or "202603" in n), gh.rel["police-2026"]
            deletes = [c[3].rsplit("/", 1)[1] for c in gh.calls if c[:3] == ("api", "-X", "DELETE")]
            assert sorted(d for d in deletes if d.endswith("202603")) == sorted(f"{n}202603" for n in police.KINDS), deletes
            assert not [n for n in gh.rel["police-2026"] if n.startswith("police_attempt_202603")], gh.rel  # done
            # the manifest is uploaded last
            ups = [os.path.basename(c[3]) for c in gh.calls if c[:2] == ("release", "upload")]
            assert ups[-1] == "police_manifest_202603.json", ups
            # a month that fails to convert leaves a mark and is not taken again by the schedule
            code, out, done = drive(gh, [], fail="2026-08")
            assert code and done == ["2026-08"], (code, out)
            assert gh.rel["police-2026"]["police_failed_202608.json"]["state"] == "uploaded"
            code, out, done = drive(gh, [])
            assert code == 0 and done == ["2026-07"], (code, done, out)
            assert "2026-08 failed before" in out
            # a month the server does not answer for (down, or not posted yet) costs no attempt, however often
            for n in range(4):
                code, out, done = drive(gh, [], unavailable=(2026, 4))
                assert str(code).startswith("not available"), (n, code, out)
            assert not [x for x in gh.rel["police-2026"] if x.startswith("police_attempt_202604")], gh.rel
            # a download that fails (or a run that dies) is tried again, three times in all, then left out
            for n in (1, 2, 3):
                code, out, done = drive(gh, [], download_fail=(2026, 4))
                assert code == "download failed (56)", (n, code, out)
                assert f"police_attempt_202604_{n}.json" in gh.rel["police-2026"], (n, gh.rel["police-2026"])
            assert "police_failed_202604.json" not in gh.rel["police-2026"]
            code, out, done = drive(gh, [])
            assert code == 0 and done == ["2026-02"], (code, done, out)
            assert "2026-04 was started 3 times" in out, out
            # asked for by name, the failed month is tried again, and on success its marks go
            code, out, done = drive(gh, ["--month", "2026-04"])
            assert code == 0 and done == ["2026-04"], (code, out)
            assert not [n for n in gh.rel["police-2026"] if "202604" in n and not n.startswith(("police_5min",
                        "police_hourly", "police_points", "police_manifest"))], gh.rel["police-2026"]
            code, out, done = drive(gh, ["--month", "2026-08"])
            assert code == 0 and done == ["2026-08"] and "police_failed_202608.json" not in gh.rel["police-2026"], out
            # a failed month listed in the retry file is taken again by the schedule
            g3 = FakeGitHub({"police-2026": {"police_failed_202608.json": {"state": "uploaded", "id": "1", "size": 1}}})
            code, out, done = drive(g3, ["--dry-run"])
            assert "2026-08 failed before" in out and "next: ['2026-07'" in out, out
            open(police.RETRY_FILE, "w").write("2026-08 the converter was fixed\n")
            code, out, done = drive(g3, ["--dry-run"])
            assert "2026-08 failed before; tried again" in out and "next: ['2026-08'" in out, out
            os.remove(police.RETRY_FILE)
            # releases are made only for a month that is worked on (the listing reads all years)
            assert set(gh.rel) == {"police-2026"}, sorted(gh.rel)
            # an upload that ends with a wrong size stops the run
            class Short(FakeGitHub):
                def __call__(self, *args):
                    r = super().__call__(*args)
                    if args[:2] == ("release", "upload"):
                        self.rel[args[2]][os.path.basename(args[3])]["size"] -= 1
                    return r
            short = Short({"police-2026": {}})
            code, out, done = drive(short, ["--month", "2026-08"])
            assert code and "after upload" in str(code), code
            assert "police_manifest_202608.json" not in short.rel["police-2026"], "manifest before the data checked"
            # an official listing that has changed stops the run (only an unreadable one falls back to the archive)
            def changed_listing():
                raise RuntimeError("the official regions changed: ['atlantis']")
            code, out, done = drive(FakeGitHub({}), ["--dry-run"], listing=changed_listing)
            assert code == "the official regions changed: ['atlantis']", code
            def unreadable():
                raise police.ListingUnavailable("timed out")
            code, out, done = drive(FakeGitHub({}), ["--dry-run"], listing=unreadable)
            assert code == 0 and "::warning::official listing: timed out" in out, (code, out)
            # --no-upload converts and checks but writes nothing, also on failure
            g2 = FakeGitHub({"police-2026": {}})
            code, out, done = drive(g2, ["--no-upload", "--month", "2026-08"], fail="2026-08")
            assert code and g2.rel["police-2026"] == {}, (code, g2.rel)
            # a malformed skip line stops the run
            open(police.SKIP_FILE, "w").write("2026-05\n")
            raises(police.skipped, SystemExit, "expected")
    finally:
        (police.collect.gh, police.official_listing, police.available, police.curl, police.convert_and_check,
         police.SKIP_FILE, police.RETRY_FILE) = saved


def test_official_listing():
    from datetime import datetime
    assert [police.fallback_last(datetime(2026, m, 15)) for m in (1, 2, 3, 12)] == \
        [(2025, 11), (2025, 12), (2026, 1), (2026, 10)]
    real = police.urllib.request.urlopen

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def listing(regions, month="2026年08月"):
        links = [f"/202610010000/typeB_{r}_2026_08.zip" for r in sorted(regions)]
        return lambda url, timeout: Resp(json.dumps([{"type": "typeA"}, {"type": "typeB", "targetMonth": month,
                                                     "targetList": [{"link": l} for l in links]}]).encode())
    try:
        police.urllib.request.urlopen = listing(convert.REGIONS)
        ym, links = police.official_listing()
        assert ym == (2026, 8) and len(links) == 51, (ym, len(links))
        # a region more or less is a change the code must follow: it stops the run, it is not a warning
        police.urllib.request.urlopen = listing(convert.REGIONS - {"akita"})
        raises(police.official_listing, RuntimeError, "regions changed")
        police.urllib.request.urlopen = listing(convert.REGIONS, month="2026-08")
        raises(police.official_listing, RuntimeError, "targetMonth")

        def down(url, timeout):
            raise police.urllib.error.URLError("timed out")
        police.urllib.request.urlopen = down
        raises(police.official_listing, police.ListingUnavailable, "timed out")
    finally:
        police.urllib.request.urlopen = real


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f()
            print("ok", name)
