#!/usr/bin/env bash
# Planted defects in scripts/daily.py: each must make scripts/test_daily.py fail. Run from the repo root with PY set.
set -u
PY=${PY:-python}
try() {  # name, python expression that edits the text s
  cp scripts/daily.py /tmp/daily.keep
  $PY - "$2" <<'EOF'
import sys
s = open("scripts/daily.py", encoding="utf-8").read()
t = eval(sys.argv[1])
assert t != s, "the mutation did not change the file"
open("scripts/daily.py", "w", encoding="utf-8").write(t)
EOF
  if $PY scripts/test_daily.py > /tmp/md.log 2>&1; then echo "SURVIVED: $1"; cp /tmp/daily.keep scripts/daily.py; exit 1; fi
  echo "caught: $1 ($(tail -1 /tmp/md.log | cut -c1-100))"
  cp /tmp/daily.keep scripts/daily.py
}
try "a day of 23 rows counts as full" 's.replace("g[s].count().eq(24) & n.eq(24)", "g[s].count().eq(n)")'
try "23 hours are enough" 's.replace("g[s].count().eq(24)", "g[s].count().ge(23)")'
try "peak takes the later hour on a tie" 's.replace("ascending=[False, True]", "ascending=[False, False]")'
try "wet from 0.5 mm" 's.replace("(d.p >= 1)", "(d.p >= 0.5)")'
try "large counted in flagged hours" 's.replace("df.down_large.astype(\"float64\")).where(df.vehicles.notna())", "df.down_large.astype(\"float64\"))")'
try "baseline counted in flagged hours" 's.replace("df.baseline_vehicles.astype(\"float64\").where(df.vehicles.notna())", "df.baseline_vehicles.astype(\"float64\")")'
try "temperature of a short day" 's.replace("g.t.mean().where(full(\"t\"))", "g.t.mean()")'
try "rain of a short day" 's.replace("g.p.sum().where(full(\"p\"))", "g.p.sum(min_count=1)")'
try "hours counts rows" 's.replace("g.v.count().astype(\"int8\")", "n.astype(\"int8\")")'
try "duplicated hours accepted" 's.replace("if (n > 24).any():", "if False:")'
try "vs_baseline not rounded" 's.replace(".astype(\"float64\")).round(3)", ".astype(\"float64\"))")'
echo "all planted defects caught"
