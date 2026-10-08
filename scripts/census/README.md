# Tying counters to the 2021 Road Traffic Census

These scripts made `data/census_2021_counters.csv`. They ran once, by hand, in a work directory laid out as
`census/kasyoNN.csv` (the census tables), `b0/hourly_2026-09_*.parquet` (the September 2026 Kaggle files) and
`repo/` (this repository), with the scripts at the top of the work directory (`match_perm.py` runs `match_census.py` from there).
They need pandas, numpy and scipy. They are kept as the record of how the table was made, not as part of the daily build.

1. Download the census 箇所別基本表 for the 47 prefectures: `https://www.mlit.go.jp/road/census/r3/data/csv/kasyoNN.csv` (NN = 01-47, cp932).
2. `geo_census.py` geocodes the observation addresses of sections in municipalities that have a counter with the GSI address search
   (`census/geocode.json`, kept here as `data/census_geocode.json`). Most resolve to the town or 字, so a point can be a few km off;
   some resolve only to the municipality or prefecture, and step 5 drops those pairs.
3. `daily_vol.py` writes each counter's September 2026 weekday median 24-hour volume, daytime large-vehicle share, day/night ratio and
   up-direction share.
4. `match_perm.py 3` assigns loop counters on national highways one to one to census sections observed by a permanent counter
   (observation date ending in 00) within 3 km, minimising |log volume ratio|.
5. `build_census.py` keeps a pair when |log volume ratio| < 0.15 and both fingerprints agree (up-direction share within 2 points and
   day/night ratio within 0.05), drops a pair whose observation address the GSI search placed only at the prefecture or
   municipality, and writes `census/census_r3_match.csv`. Its docstring has the rule and why it was tightened after the audit
   and the code review. Copy that file to `data/census_2021_counters.csv`.
6. `verify_audit.py` re-runs two checks on the kept pairs. On the 365 kept pairs, the daytime peak-hour share differs by more
   than 1 point for 3.8% of pairs (47.4% when the pairs are shuffled); this check was used to tighten the rule, so it is not fully
   independent any more. For the 99 counters whose up and down large-vehicle shares differ by more than 2 points, the sign agrees
   with the census for 93%, which also shows that the census and the API use the same up and down directions.
