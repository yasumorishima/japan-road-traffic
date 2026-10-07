Vehicle counts from the traffic counters of Japan's Ministry of Land, Infrastructure, Transport and Tourism (MLIT): about 2,000 counters on national highways and expressways, hourly for the whole country and every five minutes for the Kanto region (Tokyo and the surrounding prefectures). Both directions, small and large vehicles.

The source API keeps only about **three months of hourly values and one month of five-minute values**; older values disappear from it. This dataset collects every day with GitHub Actions and keeps them, so the history grows with each version. It starts in July 2026 (hourly) and September 2026 (five-minute).

Source code, checks and the raw daily files: https://github.com/yasumorishima/japan-road-traffic

Notebook: [a first look](https://www.kaggle.com/code/yasunorim/japan-road-traffic-a-first-look) (when traffic peaks, trucks at night, and which days were busier than usual).

## Files

| File | What |
|---|---|
| `hourly_YYYY-MM_DD-DD.parquet` | Nationwide, hourly, one file per ten days (days 01-10, 11-20, 21 to the end of the month, JST): permanent and CCTV counters |
| `five_minute_kanto_YYYY-MM_DD-DD.parquet` | Kanto box, every five minutes, one file per ten days, with camera status for CCTV rows |
| `counters.csv` | One row per counter: location, prefecture, municipality and town, last day seen (read `municipality_code` as text to keep its leading zero) |

Read a whole resolution at once with `pd.concat(pd.read_parquet(p) for p in sorted(glob.glob(".../hourly_*.parquet")))`.

### Ready-to-use columns

After the source's own columns, every Parquet file has derived columns, so most questions need no cleaning:

| Column | What |
|---|---|
| `up_vehicles`, `down_vehicles`, `vehicles` | All vehicles per direction and in both directions, one column for permanent and CCTV counters. **Empty when a permanent counter flags a fault**, so a failed counter's 0 never looks like an empty road |
| `flagged` | Permanent counters: True when any fault or missing flag is set |
| `weekday`, `is_holiday` | 0 = Monday; Japanese national and substitute holidays (Cabinet Office list) |
| `prefecture`, `prefecture_en` | Prefecture of the counter in Japanese and English, without joining `counters.csv` (drop `prefecture` before a join, or you get `prefecture_x` / `prefecture_y`) |

The key of a row is (`time_jst`, `counter_id`, `sensor`). The same ID can exist as a permanent counter (`loop`) and as a CCTV counter (`cctv`), so join `counters.csv` on both columns.

## What you can do with it

- Find when a road is busier than usual: compare a count with the same counter, weekday and hour in earlier weeks (holidays, Obon, typhoons, events).
- Forecast traffic for the next hours or days per counter, or study weekly and seasonal patterns as the history grows.
- Compare small and large vehicles (freight versus passenger traffic) by region and hour.

## Things to know before using it

- **These are counts, not speeds.** No open source of speed or congestion length could be redistributed; congestion has to be inferred, for example from counts well above the usual level for that hour.
- **CCTV counts are often empty.** The source blanks them when the camera is off its preset position, and some are empty for other reasons. Permanent-counter rows are almost always filled, but **a permanent counter that fails reports 0 vehicles with a fault or missing flag** (`*_power_failure`, `*_loop_fault`, `*_ultrasonic_fault`, `*_missing`): use `vehicles` (empty for flagged hours) or drop flagged rows before using the raw counts. Flagged hours that still carry counts are unreliable too. For permanent counters the total is small + large + unclassified; CCTV counters give the total directly (`*_total`).
- **The source lacks a few time codes on many days** (for example 287 of 288 five-minute codes; asking again returns nothing). A day is archived when at most 5% of its codes are missing; a day with a larger gap is retried and kept as it is only when it is about to leave the source window. A missing hour means the source has no value for it.
- The values are reference values, not official MLIT traffic survey results. Counters can be unpublished at times because of faults.
- There are no road names or route numbers: the API gives none, and the route sources found were non-commercial or share-alike. Place names come from the counter location.
- Times are Japan Standard Time (UTC+9).

## Source and terms

出典：「交通量 API（国土交通省）機能による交通量(参考値)」を加工して作成（データ提供：公益財団法人日本道路交通情報センター https://www.jartic-open-traffic.org/ ）

Traffic volume data from the MLIT Traffic Volume API (reference values), provided by the Japan Road Traffic Information Center (JARTIC), processed by this dataset. JARTIC states that its terms are compatible with CC BY 4.0. Place names: 出典：国土地理院 (GSI reverse geocoder). Holidays: 出典：内閣府ホームページ「国民の祝日について」 https://www8.cao.go.jp/chosei/shukujitsu/gaiyou.html （公共データ利用規約 第1.0版）を加工して作成 (`is_holiday`). This dataset is not made or endorsed by MLIT, JARTIC, GSI or the Cabinet Office.

If you use the data, please credit the source as above.
