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
   and the code review, and a third amendment (2026-10-17, found while building the road graph) that swaps two ties when
   neither town name is in its census address and both are after the swap with volume and fingerprints still passing
   (one case: 3110650 and 5114680 on route 246 at the Kanagawa-Shizuoka border).
6. `verify_audit.py` re-runs two checks on the kept pairs. On the 365 kept pairs, the daytime peak-hour share differs by more
   than 1 point for 3.8% of pairs (47.4% when the pairs are shuffled); this check was used to tighten the rule, so it is not fully
   independent any more. For the 99 counters whose up and down large-vehicle shares differ by more than 2 points, the sign agrees
   with the census for 93%, which also shows that the census and the API use the same up and down directions.
7. `build_graph.py` (2026-10-17) joins every census section (all road types) into one road network on the census's own
   connection columns, places each kept counter on the sections of its survey unit in its own municipality, and writes
   `census/road_graph_2021.csv` (pairs within 60 km by road) and `census/census_r3_placed.csv` (`census_r3_match.csv` plus
   `placed_from_section`, `placed_to_section`, `placed_km`, `placed_in_municipality`). Copy them to `data/road_graph_2021.csv` and
   `data/census_2021_counters.csv`. Its docstring has the joining rules, a variant that was tried and dropped, and the checks
   (it stops when more than 1% of pairs have a straight line longer than the road allows; 4 of 1,747 did on 2026-10-17).
8. `graph_validate.py census/road_graph_2021.csv <dir of hourly_*.parquet>` is a check that was not used to build the graph:
   within straight-line distance bands, pairs closer by road have more correlated hourly `vs_baseline`.
