Vehicle counts from the traffic counters of Japan's Ministry of Land, Infrastructure, Transport and Tourism (MLIT): about 2,000 counters on national highways and expressways, hourly for the whole country and every five minutes for the Kanto region (Tokyo and the surrounding prefectures). Both directions, small and large vehicles.

The source API keeps only about **three months of hourly values and one month of five-minute values**; older values disappear from it. This dataset collects every day with GitHub Actions and keeps them, so the history grows with each version. It starts in July 2026 (hourly) and September 2026 (five-minute).

Source code, checks and the raw daily files: https://github.com/yasumorishima/japan-road-traffic

Notebook: [a first look](https://www.kaggle.com/code/yasunorim/japan-road-traffic-a-first-look) (when traffic peaks, trucks at night, and which days were busier than usual).

## Files

| File | What |
|---|---|
| `daily_summary.csv` | **Start here.** One row per counter and day (about 2,000 counters): vehicles, large vehicles, the busiest hour, the usual count for that weekday (`baseline_vehicles`, `vs_baseline`), rain and temperature at the nearest weather station, and the calendar (holidays, runs of days off). Day totals are given only for days with all 24 hours (`hours`); a day of 24 counted hours with fewer vehicles than hours (mostly 0 all day) is a stopped camera or counter (`stopped`): it has no vehicle counts and is not used for the baseline |
| `hourly_YYYY-MM_DD-DD.parquet` | Nationwide, hourly, one file per ten days (days 01-10, 11-20, 21 to the end of the month, JST): permanent and CCTV counters |
| `five_minute_kanto_YYYY-MM_DD-DD.parquet` | Kanto box, every five minutes, one file per ten days, with camera status for CCTV rows |
| `counters.csv` | One row per counter: location, prefecture and municipality in Japanese and English (`prefecture_en`, `municipality_en`, e.g. Yokohama-shi Tsurumi-ku), town (Japanese), last day seen (read `municipality_code` as text to keep its leading zero) |
| `weather_hourly_YYYY-MM_DD-DD.parquet` | Hourly weather observations at every JMA AMeDAS station (about 1,290), one file per ten days, from 2026-09-29: rain, temperature, wind, sunshine, snow, humidity, pressure |
| `weather_stations.csv` | AMeDAS stations: ID, location, altitude, names in Japanese, kana and English |
| `calendar.csv` | One row per day: holidays with their Japanese and English names, days off, the length of the run of days off, the evening before a day off, Obon and New Year |
| `census_2021_counters.csv` | 365 permanent counters on national highways tied to their section of the 2021 Road Traffic Census: route name (Japanese and English, e.g. National Route 5), lanes, speed limit, and 2021 rush-hour and off-peak travel speeds per direction. The tie is inferred (see the `match_` columns); the speeds are 2021 values, not live ones |
| `road_graph_2021.csv` | Road distance between those 365 counters on the census road network, for graph models (spatio-temporal GNNs and the like): one row per pair within 60 km by road (1,777 pairs; 352 counters have at least one), the straight-line distance, whether both are on the same route, and `direct` (no other counter in between, 1,016 pairs) |

Read a whole resolution at once with `pd.concat(pd.read_parquet(p) for p in sorted(glob.glob(".../hourly_*.parquet")))`.

### Ready-to-use columns

After the source's own columns, every Parquet file has derived columns, so most questions need no cleaning (the five-minute files stop at `prefecture_en`; join `calendar.csv` on the date and the hourly weather on the hour):

| Column | What |
|---|---|
| `up_vehicles`, `down_vehicles`, `vehicles` | All vehicles per direction and in both directions, one column for permanent and CCTV counters. **Empty when a permanent counter flags a fault**, so a failed counter's 0 never looks like an empty road |
| `flagged` | Permanent counters: True when any fault or missing flag is set |
| `weekday`, `is_holiday` | 0 = Monday; Japanese national and substitute holidays (Cabinet Office list) |
| `day_off`, `days_off_in_run`, `customary_break` | Hourly files: weekend or holiday; length of the run of days off it belongs to (2 = a weekend, 3 = a three-day weekend, 5 = Silver Week 2026, 0 = a working day); `obon` (August 13-16) or `new_year` (December 29 - January 3), which are not days off by law |
| `baseline_vehicles`, `vs_baseline` | Hourly files: the usual count for this counter and time: the median of `vehicles` on the 4 most recent earlier days of the same weekday within 35 days, at least 3 of them (holidays count as Sunday; Obon and New Year days and stopped days, with fewer vehicles than hours, are left out), and the ratio to it (1.3 = 30% more than usual). **Built from earlier days only**, so it can be used as a forecasting feature. A camera or counter that stops for only part of a day is not detected and pulls the baseline down, so a very large `vs_baseline` usually follows such a stop |
| `precip_mm`, `temp_c`, `wind_ms`, `snow_depth_cm` | Hourly files: weather at the counter's nearest AMeDAS station that reports it (rain within 10 km, the others within 20 km; the station and its distance are in `counters.csv`). Rain is the amount in the hour of `time_jst`. Empty before 2026-09-29 |
| `prefecture`, `prefecture_en` | Prefecture of the counter in Japanese and English, without joining `counters.csv` (drop `prefecture` and `prefecture_en` before joining `counters.csv`, or you get `_x` / `_y` columns) |

The key of a row is (`time_jst`, `counter_id`, `sensor`). The same ID can exist as a permanent counter (`loop`) and as a CCTV counter (`cctv`), so join `counters.csv` on both columns.

## What you can do with it

- Find when a road is busier than usual: compare a count with the same counter, weekday and hour in earlier weeks (holidays, Obon, typhoons, events).
- Forecast traffic for the next hours or days per counter, or study weekly and seasonal patterns as the history grows.
- Compare small and large vehicles (freight versus passenger traffic) by region and hour.
- See how rain, snow and temperature change traffic: the hourly files carry the nearest station's weather, and `vs_baseline` compares each hour with the same counter's usual.

## Things to know before using it

- **These are counts, not speeds.** No open source of live speed or congestion length could be redistributed; congestion has to be inferred, for example from counts well above the usual level for that hour. `census_2021_counters.csv` gives a static 2021 travel speed for 365 counters.
- **CCTV counts are often empty.** The source blanks them when the camera is off its preset position, and some are empty for other reasons. Permanent-counter rows are almost always filled, but **a permanent counter that fails reports 0 vehicles with a fault or missing flag** (`*_power_failure`, `*_loop_fault`, `*_ultrasonic_fault`, `*_missing`): use `vehicles` (empty for flagged hours) or drop flagged rows before using the raw counts. Flagged hours that still carry counts are unreliable too. For permanent counters the total is small + large + unclassified; CCTV counters give the total directly (`*_total`).
- **The source lacks a few time codes on many days** (for example 287 of 288 five-minute codes; asking again returns nothing). A day is archived when at most 5% of its codes are missing; a day with a larger gap is retried and kept as it is only when it is about to leave the source window. A missing hour means the source has no value for it.
- The values are reference values, not official MLIT traffic survey results. Counters can be unpublished at times because of faults.
- The API gives no road names or route numbers. Route names come only from the 2021 census, for the 365 counters in `census_2021_counters.csv`; the other route sources found were non-commercial or share-alike. Place names come from the counter location.
- Weather starts on 2026-09-29 (the JMA site keeps its hourly maps for only about nine days; that day lacks 02:00 to 08:00). Only values with JMA's normal quality flag are kept, and JMA flags snow depth outside the snow season, so `snow_depth_cm` is empty then.
- Times are Japan Standard Time (UTC+9).

## Source and terms

出典：「交通量 API（国土交通省）機能による交通量(参考値)」を加工して作成（データ提供：公益財団法人日本道路交通情報センター https://www.jartic-open-traffic.org/ ）

Traffic volume data from the MLIT Traffic Volume API (reference values), provided by the Japan Road Traffic Information Center (JARTIC), processed by this dataset. JARTIC states that its terms are compatible with CC BY 4.0. Place names: 出典：国土地理院 (GSI reverse geocoder). Holidays: 出典：内閣府ホームページ「国民の祝日について」 https://www8.cao.go.jp/chosei/shukujitsu/gaiyou.html （公共データ利用規約 第1.0版）を加工して作成 (`is_holiday`). English municipality names: 出典：「全国地方公共団体コード」（総務省）（https://www.soumu.go.jp/denshijiti/code.html）を加工して作成 (type of municipality), names from Wikidata (CC0). Road Traffic Census (`census_2021_counters.csv`): 出典：「令和3年度全国道路・街路交通情勢調査（道路交通センサス）一般交通量調査 箇所別基本表」（国土交通省）（https://www.mlit.go.jp/road/census/r3/index.html）を加工して作成 (公共データ利用規約 第1.0版); the tie between counters and census sections is made by this dataset, with observation addresses located by the GSI address search (出典：国土地理院). Weather: 出典：気象庁ホームページ（https://www.jma.go.jp/bosai/amedas/）のアメダス観測値を加工して作成 (Japan Meteorological Agency AMeDAS observations, Public Data License 1.0; only observations, no forecast or warning). This dataset is not made or endorsed by MLIT, JARTIC, GSI, the Cabinet Office, MIC or JMA.

If you use the data, please credit the source as above.
