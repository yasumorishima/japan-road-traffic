#!/bin/bash
# Planted defects in convert.py and verify.py: each must make test_police.py fail. Run from the repo root.
set -u
# no bytecode cache: a test run shorter than a second can otherwise import the previous mutation's cached
# bytecode (same mtime second, same size) and report on the wrong defect
export PYTHONDONTWRITEBYTECODE=1
cd scripts/police
rm -rf __pycache__
fail=0
try() {  # file, name, python expression that edits the text s
  cp "$1" /tmp/police.keep
  python - "$1" "$3" <<'EOF'
import sys
p, expr = sys.argv[1], sys.argv[2]
s = open(p, encoding="utf-8").read()
t = eval(expr)
assert t != s, "the mutation did not change the file"
open(p, "w", encoding="utf-8").write(t)
EOF
  if [ $? -ne 0 ]; then echo "MUTATION DID NOT APPLY: $2"; fail=1; cp /tmp/police.keep "$1"; return; fi
  if python test_police.py > /tmp/pm.log 2>&1; then echo "SURVIVED: $2"; fail=1
  else echo "caught: $2 ($(tail -1 /tmp/pm.log | cut -c1-110))"; fi
  cp /tmp/police.keep "$1"
}
C=convert.py
try $C "point 0 kept" 's.replace("& (pl.col(\"point_i\") != 0)", "")'
try $C "identical member kept" 's.replace("        if h in seen:\n", "        if False:\n")'
try $C "off-mark minutes kept" 's.replace("& (pl.col(\"time_jst\").dt.minute() % 5 == 0)", "")'
try $C "month end included" 's.replace("(pl.col(\"time_jst\") < end)", "(pl.col(\"time_jst\") <= end + pl.duration(days=31))")'
try $C "conflicting time kept" 's.replace("five = (g.filter(pl.col(\"nv\") == 1)", "five = (g.filter(pl.col(\"nv\") >= 1)")'
try $C "n_5min counts missing values" 's.replace("pl.col(\"vehicles\").count().cast(pl.Int8)", "pl.len().cast(pl.Int8)")'
try $C "empty hour reads as zero" 's.replace("then(pl.col(\"vehicles\")).otherwise(None)", "then(pl.col(\"vehicles\")).otherwise(0)")'
try $C "seconds format lost" 's.replace("TIME_FORMATS = [\"%Y/%m/%d %H:%M\", \"%Y/%m/%d %H:%M:%S\"]", "TIME_FORMATS = [\"%Y/%m/%d %H:%M\"]")'
try $C "bad count read as zero" 's.replace(".otherwise(None)\n                         .cast(pl.Int16)", ".otherwise(0)\n                         .cast(pl.Int16)")'
try $C "outside gate at its level" 's.replace("> MAX_OUTSIDE_SHARE", ">= MAX_OUTSIDE_SHARE")'
try $C "conflict gate at its level" 's.replace("> MAX_CONFLICT_SHARE", ">= MAX_CONFLICT_SHARE")'
try $C "bad value gate at its level" 's.replace("> MAX_BAD_VALUE_SHARE", ">= MAX_BAD_VALUE_SHARE")'
try $C "region gate" 's.replace("< MIN_REGIONS", "< MIN_REGIONS - 1")'
try $C "point gate" 's.replace("< MIN_POINTS", "< MIN_POINTS - 1")'
try $C "unknown column accepted" 's.replace("        if len(hit) != 1:\n            raise", "        if len(hit) > 1:\n            raise")'
try $C "region twice accepted" 's.replace("    if dup:\n        raise", "    if False:\n        raise")'
try $C "name from first row, empty allowed" 's.replace("names = Counter(x for x in r[\"names\"] if x)", "names = Counter(r[\"names\"][-1:])")'
V=verify.py
try $V "verify ignores vehicles" 's.replace("if not ((va == vb).fillna(False) | (va.isna() & vb.isna())).all():", "if False:")'
try $V "verify keeps conflicts" 's.replace("        if len(vals) != 1:\n            continue", "        if False:\n            continue")'
try $V "verify keeps point 0" 's.replace("or int(pt) == 0", "")'
exit $fail
