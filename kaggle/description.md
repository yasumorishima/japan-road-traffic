Vehicle counts from the traffic counters of Japan's Ministry of Land, Infrastructure, Transport and Tourism (MLIT): about 2,000 counters on national highways and expressways, hourly for the whole country and every five minutes for the Kanto region (Tokyo and the surrounding prefectures). Both directions, small and large vehicles.

The source API keeps only about **three months of hourly values and one month of five-minute values**; older values disappear from it. This dataset collects every day with GitHub Actions and keeps them, so the history grows with each version. It starts in July 2026 (hourly) and September 2026 (five-minute).

Source code, checks and the raw daily files: https://github.com/yasumorishima/japan-road-traffic

Notebook: [a first look](https://www.kaggle.com/code/yasunorim/japan-road-traffic-a-first-look) (when traffic peaks, trucks at night, and which days were busier than usual).

## Files

| File | What |
|---|---|
| `hourly_YYYY-MM.parquet` | Nationwide, hourly, one file per month (JST): permanent and CCTV counters |
| `five_minute_kanto_YYYY-MM.parquet` | Kanto box, every five minutes, one file per month, with camera status for CCTV rows |
| `counters.csv` | One row per counter: location, prefecture, municipality and town, last day seen (read `municipality_code` as text to keep its leading zero) |

The key of a row is (`time_jst`, `counter_id`, `sensor`). The same ID can exist as a permanent counter (`loop`) and as a CCTV counter (`cctv`), so join `counters.csv` on both columns.

## What you can do with it

- Find when a road is busier than usual: compare a count with the same counter, weekday and hour in earlier weeks (holidays, Obon, typhoons, events).
- Forecast traffic for the next hours or days per counter, or study weekly and seasonal patterns as the history grows.
- Compare small and large vehicles (freight versus passenger traffic) by region and hour.

## Things to know before using it

- **These are counts, not speeds.** No open source of speed or congestion length could be redistributed; congestion has to be inferred, for example from counts well above the usual level for that hour.
- **CCTV counts are often empty.** The source blanks them when the camera is off its preset position, and some are empty for other reasons. Permanent-counter rows are almost always filled, but **a permanent counter that fails reports 0 vehicles with a fault or missing flag** (`*_power_failure`, `*_loop_fault`, `*_ultrasonic_fault`, `*_missing`): drop flagged rows before using the counts. For permanent counters the total is small + large + unclassified; CCTV counters give the total directly (`*_total`).
- **The source lacks a few time codes on many days** (for example 287 of 288 five-minute codes; asking again returns nothing). A day is archived when at most 5% of its codes are missing; a day with a larger gap is retried and kept as it is only when it is about to leave the source window. A missing hour means the source has no value for it.
- The values are reference values, not official MLIT traffic survey results. Counters can be unpublished at times because of faults.
- There are no road names or route numbers: the API gives none, and the route sources found were non-commercial or share-alike. Place names come from the counter location.
- Times are Japan Standard Time (UTC+9).

## Source and terms

出典：「交通量 API（国土交通省）機能による交通量(参考値)」を加工して作成（データ提供：公益財団法人日本道路交通情報センター https://www.jartic-open-traffic.org/ ）

Traffic volume data from the MLIT Traffic Volume API (reference values), provided by the Japan Road Traffic Information Center (JARTIC), processed by this dataset. JARTIC states that its terms are compatible with CC BY 4.0. Place names: 出典：国土地理院 (GSI reverse geocoder). This dataset is not made or endorsed by MLIT, JARTIC or GSI.

If you use the data, please credit the source as above.
