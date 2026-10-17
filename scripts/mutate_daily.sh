#!/usr/bin/env bash
# Planted defects in scripts/daily.py: each must make scripts/test_daily.py fail. Run from the repo root with PY set.
set -u
# no bytecode cache: a quick test run can otherwise import the previous mutation's cached bytecode
export PYTHONDONTWRITEBYTECODE=1
rm -rf scripts/__pycache__
PY=${PY:-python}
tmp=$(mktemp -d)
cp scripts/daily.py "$tmp/daily.keep"
# also on Ctrl-C or an error: put the original back before its copy goes
trap 'cp "$tmp/daily.keep" scripts/daily.py; rm -rf "$tmp"' EXIT
try() {  # name, python expression that edits the text s
  if ! $PY - "$2" <<'EOF'
import sys
s = open("scripts/daily.py", encoding="utf-8").read()
t = eval(sys.argv[1])
assert t != s, "the mutation did not change the file"
open("scripts/daily.py", "w", encoding="utf-8").write(t)
EOF
  then echo "BROKEN MUTATION (the text to replace is not in daily.py): $1"; cp "$tmp/daily.keep" scripts/daily.py; exit 1; fi
  if $PY scripts/test_daily.py > "$tmp/md.log" 2>&1; then echo "SURVIVED: $1"; cp "$tmp/daily.keep" scripts/daily.py; exit 1; fi
  echo "caught: $1 ($(tail -1 "$tmp/md.log" | cut -c1-100))"
  cp "$tmp/daily.keep" scripts/daily.py
}
try "a day of 23 rows counts as full" 's.replace("g[s].count().eq(24) & n.eq(24)", "g[s].count().eq(n)")'
try "23 hours are enough" 's.replace("g[s].count().eq(24)", "g[s].count().ge(23)")'
try "peak takes the later hour on a tie" 's.replace("ascending=[False, True]", "ascending=[False, False]")'
try "wet from 0.5 mm" 's.replace("(d.p >= 1)", "(d.p >= 0.5)")'
try "large of a day with a flagged hour" 's.replace("g.lg.sum().where(full(\"lg\") & counted)", "g.lg.sum().where(full(\"lg\"))")'
try "baseline counted in flagged hours" 's.replace("df.baseline_vehicles.astype(\"float64\").where(df.vehicles.notna())", "df.baseline_vehicles.astype(\"float64\")")'
try "temperature of a short day" 's.replace("g.t.mean().where(full(\"t\"))", "g.t.mean()")'
try "rain of a short day" 's.replace("g.p.sum().where(full(\"p\"))", "g.p.sum(min_count=1)")'
try "hours counts rows" 's.replace("g.v.count().astype(\"int8\")", "n.astype(\"int8\")")'
try "duplicated hours accepted" 's.replace("if (n > 24).any():", "if False:")'
try "vs_baseline not rounded" 's.replace(".where(bl > 0).round(3)", ".where(bl > 0)")'
try "baseline 0 divides" 's.replace(".where(bl > 0).round(3)", ".round(3)")'
try "stopped days counted" 's.replace("counted = full(\"v\") & ~stopped", "counted = full(\"v\")")'
try "stopped flag off" 's.replace("out[\"stopped\"] = stopped.astype(bool)", "out[\"stopped\"] = False")'
try "only 24 zero hours are a stop" 's.replace("g.v.sum().lt(STOP_BELOW)", "g.v.sum().eq(0)")'
try "24 vehicles are a stop" 's.replace("g.v.sum().lt(STOP_BELOW)", "g.v.sum().le(STOP_BELOW)")'
try "temperature of a day with a missing reading" 's.replace("g.t.mean().where(full(\"t\"))", "g.t.mean().where(n.eq(24))")'
try "baseline 1 left out" 's.replace(".where(bl > 0).round(3)", ".where(bl > 1).round(3)")'
try "divided by the unrounded baseline" 's.replace("(out.vehicles.astype(\"float64\") / bl)", "(out.vehicles.astype(\"float64\") / g.b.sum().where(full(\"b\")))")'
echo "all planted defects caught"
