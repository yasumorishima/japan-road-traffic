"""Planted defects in convert.py, verify.py and police.py: each must make test_police.py fail.
Run from anywhere: python scripts/police/mutate_police.py (mutate_police.sh calls it)."""
import os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
C, V, P = "convert.py", "verify.py", "police.py"
M = [
    # convert: what is dropped and counted
    (C, "point 0 kept", '& (pl.col("point_i") != 0)', ""),
    (C, "non-integer point kept as -1", 'pl.col("point").cast(pl.Int32, strict=False).alias("point_i")',
     'pl.col("point").cast(pl.Int32, strict=False).fill_null(-1).alias("point_i")'),
    (C, "identical member kept", "        if h in seen:\n", "        if False:\n"),
    (C, "off-mark minutes kept", '& (pl.col("time_jst").dt.minute() % 5 == 0)', ""),
    (C, "seconds kept", '\n               & (pl.col("time_jst").dt.second() == 0)).fill_null(False)', ").fill_null(False)"),
    (C, "month end included", '(pl.col("time_jst") < end)', '(pl.col("time_jst") <= end + pl.duration(days=31))'),
    (C, "month starts a day early", '(pl.col("time_jst") >= start)', '(pl.col("time_jst") >= start - pl.duration(days=1))'),
    (C, "conflicting time kept", 'five = g.filter(pl.col("nv") == 1)', 'five = g.filter(pl.col("nv") >= 1)'),
    (C, "conflicts miscounted", 'side="right")', 'side="left")'),
    (C, "negative counts kept", 'ok_v = (pl.col("v") >= 0) & (pl.col("v") <= 32767)', 'ok_v = (pl.col("v") <= 32767)'),
    (C, "bad count read as zero", 'pl.when(ok_v).then(pl.col("v")).otherwise(None).cast(pl.Int16)',
     'pl.when(ok_v).then(pl.col("v")).otherwise(0).cast(pl.Int16)'),
    (C, "seconds format lost", 'TIME_FORMATS = ["%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S"]', 'TIME_FORMATS = ["%Y/%m/%d %H:%M"]'),
    (C, "n_5min counts missing values", 'pl.col("vehicles").count().cast(pl.Int8)', 'pl.len().cast(pl.Int8)'),
    (C, "empty hour reads as zero", 'then(pl.col("vehicles")).otherwise(None)', 'then(pl.col("vehicles")).otherwise(0)'),
    # convert: points table
    (C, "point rows lose the first key", ' + g["k"].cast(pl.Int64).gather(pf)', ""),
    (C, "least common attribute", 'descending=[False, False, True, False]', 'descending=[False, False, False, False]'),
    # convert: what stops the month
    (C, "unknown column accepted", "        if len(hit) != 1:\n            raise", "        if len(hit) > 1:\n            raise"),
    (C, "region twice accepted", "        if region in seen:\n            raise", "        if False:\n            raise"),
    (C, "other member of a monthly archive accepted", "                if not m:\n                    raise ValueError",
     "                if False:\n                    raise ValueError"),
    (C, "non-csv member accepted", '            else:\n                raise ValueError(f"{path}: unexpected member {name!r}")',
     "            else:\n                continue"),
    (C, "lzh link accepted", 'if not name.lower().endswith(".csv") or "|" in name:', 'if not name.lower().endswith(".csv"):'),
    (C, "lzh test skipped", '    if r.returncode != 0:\n        raise ValueError(f"{path}: lha test failed',
     '    if False:\n        raise ValueError(f"{path}: lha test failed'),
    (C, "lzh size unchecked", "if r.returncode != 0 or os.path.getsize(allp) != sum(o for _, o in files):",
     "if r.returncode != 0:"),
    (C, "lzh end unchecked", "if f.tell() + packed > size:", "if False:"),
    (C, "lzh header method unchecked", "if len(head) < 21 or not LZH_METHOD.match(head[2:7]):", "if len(head) < 21:"),
    (C, "lzh level-2 file name ignored", "                    if ext[0] == 1:\n                        name = ext[1:-2]\n                    elif ext[0] == 2:\n                        dirname = ext[1:-2]\n                    p += nxt",
     "                    if False:\n                        name = ext[1:-2]\n                    elif ext[0] == 2:\n                        dirname = ext[1:-2]\n                    p += nxt"),
    # convert: gates
    (C, "share gates far too loose", "if s[k] / rows > lim:", "if s[k] / rows > lim * 100:"),
    (C, "share gate at its level", "if s[k] / rows > lim:", "if s[k] / rows >= lim:"),
    (C, "share diluted by point 0", 'rows = max(s["rows_in"] - s["point_zero_rows"], 1)', 'rows = max(s["rows_in"], 1)'),
    (C, "empty region passes", 'if s["rows_out"] == 0:', "if False:"),
    (C, "every region expected early", "expected = REGIONS - EARLY_MISSING.get((year, month), frozenset())",
     "expected = REGIONS - REGIONS"),
    (C, "early month missing region passes", "    elif expected - names:", "    elif False:"),
    (C, "early month list wrong", '(2017, 1): _M13 - {"hiroshima"}', "(2017, 1): _M13"),
    (C, "last time from the first row", 'pl.col("time_jst").gather(pe).alias("last_jst")', 'pl.col("time_jst").gather(pf).alias("last_jst")'),
    (C, "empty source loses its attributes", '        lf = lf.with_columns(pl.col("source").fill_null(""))\n', ""),
    (C, "lzh level-1 size not reduced", "                    packed -= nxt\n", ""),
    (C, "lzh short level-0 header accepted",
     "if len(rest) != want or len(rest) < 3 or len(rest) < 3 + rest[0] + 3 * (level == 1):", "if False:"),
    (C, "unknown region passes", "if names - REGIONS:", "if False:"),
    (C, "point gate", 'if tot["points"] < MIN_POINTS:', 'if tot["points"] < MIN_POINTS - 1:'),
    (C, "bad points not totalled", '"point_not_integer", "point_zero_rows",\n', '"point_zero_rows",\n'),
    (C, "lzh level-1 header length unchecked", "+ rest[0] + 3 * (level == 1):", "+ rest[0]:"),
    # verify
    (V, "verify ignores 5-minute values", "nbad += w != (None if pd.isna(v) else int(v))", "nbad += 0"),
    (V, "verify ignores hourly values", "nbad += ((tot if cnt else None), cnt) != ((None if pd.isna(v) else int(v)), int(n))",
     "nbad += 0"),
    (V, "verify ignores extra 5-minute keys", '            if w is missing:\n                raise SystemExit(f"{ym} {region}: 5-minute keys',
     '            if False:\n                raise SystemExit(f"{ym} {region}: 5-minute keys'),
    (V, "verify ignores missing 5-minute keys", '        if five:\n            raise SystemExit', '        if False:\n            raise SystemExit'),
    (V, "verify ignores extra hourly keys", '            if w is missing:\n                raise SystemExit(f"{ym} {region}: hourly keys',
     '            if False:\n                raise SystemExit(f"{ym} {region}: hourly keys'),
    (V, "verify ignores missing hourly keys", '        if hourly:\n            raise SystemExit', '        if False:\n            raise SystemExit'),
    (V, "verify misses a doubled 5-minute row", 'if g5.duplicated(["source", "point", "time_jst"]).any():', "if False:"),
    (V, "verify misses a doubled hour", 'if gh.duplicated(["source", "point", "hour_jst"]).any():', "if False:"),
    (V, "verify ignores the point list", "if got_points != set(points) or len(mine) != len(points):", "if False:"),
    (V, "verify ignores point attributes", '        if bad:\n            raise SystemExit(f"{ym} {region}: point attributes',
     '        if False:\n            raise SystemExit(f"{ym} {region}: point attributes'),
    (V, "verify first time is the last row", 'p["first"], p["last"] = min(p["first"], when), max(p["last"], when)',
     'p["first"], p["last"] = when, when'),
    (V, "verify ignores the manifest regions", 'if sorted(man["regions"]) != sorted(all_regions):', "if False:"),
    (V, "verify keeps conflicts", "elif old is not CONFLICT and old != val:", "elif False:"),
    (V, "verify keeps point 0", "or int(pt) == 0", ""),
    (V, "verify keeps counts above the cap", "0 <= int(v) <= 32767", "0 <= int(v)"),
    (V, "verify keeps negative counts", "0 <= int(v) <= 32767", "int(v) <= 32767"),
    (V, "verify keeps seconds", "or mi % 5 or s:", "or mi % 5:"),
    (V, "verify never takes the lzh region", 'chosen |= {f[1] for f in found if f[2] == "lzh"}', "chosen |= set()"),
    (V, "verify accepts other members", "if not m or (int(m.group(2)), int(m.group(3))) != (year, month):",
     "if False:"),
    (V, "verify reads full-width digits", 'INT_RE = re.compile(r"[+-]?\\d+", re.ASCII)', 'INT_RE = re.compile(r"[+-]?\\d+")'),
    (V, "verify takes points beyond Int32", "not -2**31 <= int(pt) < 2**31 or ", ""),
    (V, "verify trips on a short row", 'x = c[i].strip() if i is not None and i < len(c) else ""',
     'x = c[i].strip() if i is not None else ""'),
    # police
    (P, "attempt counted before the server answers", "    for url, _ in urls:\n        available(url)\n    started()\n",
     "    started()\n    for url, _ in urls:\n        available(url)\n"),
    (P, "attempts never stop a month", "if len(attempts(have, y, m)) >= MAX_ATTEMPTS:", "if False:"),
    (P, "attempt not marked", '            _note(tag, ATTEMPT_PREFIX.format(t=f"{y:04d}{m:02d}") + f"{n}.json", a.work, month=ym, attempt=n)\n', ""),
    (P, "attempts kept after success", 'for name in [FAILED.format(t=f"{y:04d}{m:02d}"), *attempts(have, y, m)]:',
     'for name in [FAILED.format(t=f"{y:04d}{m:02d}")]:'),
    (P, "changed listing only warns", "    except ListingUnavailable as e:", "    except Exception as e:"),
    (P, "fallback one month off", "(now.year, now.month - 2) if now.month > 2", "(now.year, now.month - 1) if now.month > 2"),
    (P, "releases made by the listing", "            cache[tag] = assets(tag) or {}", "            cache[tag] = ensure_release(tag)"),
    (P, "manifest before the data are checked", "        for group in (data, [manifest]):", "        for group in ([*data, manifest],):"),
    (P, "unfinished asset counts as uploaded",
     'return all(have.get(n, {}).get("state") == "uploaded" for n in names_for(y, m))',
     "return all(n in have for n in names_for(y, m))"),
    (P, "failed month taken again", "            elif failed(have, y, m):", "            elif False:"),
    (C, "own-name zip not read", 'elif not nested and name.rsplit("/", 1)[-1] == own_name:', "elif False:"),
    (C, "own-name zip read at any depth", 'elif not nested and name.rsplit', "elif name.rsplit"),
    (C, "any zip read", 'name.rsplit("/", 1)[-1] == own_name:', 'name.lower().endswith(".zip"):'),
    (V, "verify does not read the own-name zip", "if not nested and m and", "if False and m and"),
    (V, "verify reads another region's zip", "== (region, year, month):", "== (m.group(1), year, month):"),
    (P, "retry list ignored", "            if failed(have, y, m) and ym in retry:", "            if False:"),
    (P, "retry list retries every failed month", "            if failed(have, y, m) and ym in retry:",
     "            if failed(have, y, m):"),
    (P, "oldest month first", "todo.sort(key=lambda t: -(t[0] * 12 + t[1]))", "todo.sort(key=lambda t: t[0] * 12 + t[1])"),
    (P, "stale files kept", "            if name in have:\n                delete_asset(name, have[name])",
     "            if False:\n                delete_asset(name, have[name])"),
    (P, "uploaded size unchecked", 'if got.get("state") != "uploaded" or got.get("size") != want:',
     'if got.get("state") != "uploaded":'),
    (P, "failure marked with --no-upload", "if isinstance(e, KeyboardInterrupt) or a.no_upload:",
     "if isinstance(e, KeyboardInterrupt):"),
    (P, "failure not marked", '            mark_failed(tag, y, m, f"{type(e).__name__}: {e}", a.work)\n', ""),
    (P, "manifest not last", '"police_points_{t}.csv.gz", "police_manifest_{t}.json")',
     '"police_manifest_{t}.json", "police_points_{t}.csv.gz")'),
    (P, "malformed skip line accepted", 'if not re.fullmatch(r"\\d{4}-\\d{2}", ym) or not why.strip():', "if False:"),
]


def main():
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}  # never import the previous mutation's cached bytecode
    shutil.rmtree(os.path.join(HERE, "__pycache__"), ignore_errors=True)
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        for fname, what, old, new in M:
            path = os.path.join(HERE, fname)
            src = open(path, encoding="utf-8").read()
            if old not in src:
                print(f"MUTATION DID NOT APPLY: {what}", flush=True)
                bad += 1
                continue
            open(path, "w", encoding="utf-8").write(src.replace(old, new))
            try:
                log = os.path.join(tmp, "log")
                t0 = time.time()
                with open(log, "w") as f:
                    try:
                        r = subprocess.run([sys.executable, "-X", "faulthandler", "test_police.py"], cwd=HERE, env=env,
                                           stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, timeout=180)
                    except subprocess.TimeoutExpired:
                        r = None
                last = (open(log, encoding="utf-8", errors="replace").read().strip().splitlines() or [""])[-1]
                if r is None:  # a test that hangs is a broken test, not a caught defect
                    print(f"HUNG: {what} ({last[:110]})", flush=True)
                    bad += 1
                elif r.returncode == 0:
                    print(f"SURVIVED: {what}", flush=True)
                    bad += 1
                else:
                    print(f"caught: {what} [{time.time() - t0:.0f}s] ({last[:110]})", flush=True)
            finally:
                open(path, "w", encoding="utf-8").write(src)
    print(f"{len(M)} planted defects, {bad} not caught")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
