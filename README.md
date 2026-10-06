# Japan Road Traffic Volume (daily archive)

Vehicle counts from the traffic counters of Japan's Ministry of Land, Infrastructure, Transport and Tourism (MLIT) on national highways and expressways, collected every day and kept.

- **Nationwide, hourly**: permanent counters and AI counters on CCTV images, both directions, small and large vehicles (on 2026-10-05: 993 permanent and 981 CCTV counters on national highways, 76 on expressways).
- **Kanto, every 5 minutes**: the same kinds of counters inside the box longitude 138.4–140.95, latitude 34.85–37.2 (Tokyo, Kanagawa, Saitama, Chiba, Ibaraki, Tochigi, Gunma, and the edges of neighbouring prefectures).

The source API only keeps **5-minute values for about one month and hourly values for about three months**. Older values are gone from the source, so this archive is the only place they remain.

## Why this exists

Japan's Ministry of Land, Infrastructure, Transport and Tourism (MLIT) opened this API in May 2025, but it is a rolling window. There is no long history to study weekday patterns, holidays (Golden Week, Obon, New Year), weather, or events. This repository collects every day so that history builds up.

## Files

Every day is stored as release assets in the release `raw-YYYYMM` (JST month):

| asset | contents |
|---|---|
| `loop_1h_road3_YYYYMMDD.csv.gz` | hourly, permanent counters, national highways (一般国道) |
| `cctv_1h_road3_YYYYMMDD.csv.gz` | hourly, AI counters on CCTV images, national highways |
| `loop_1h_road1_…` / `cctv_1h_road1_…` | hourly, expressways (高速自動車国道) with MLIT counters |
| `loop_5m_road3_…` / `cctv_5m_road3_…` / `loop_5m_road1_…` | 5-minute, Kanto box (there are no CCTV counters on expressways in this box, so there is no `cctv_5m_road1`) |

Columns are the source's own names (Japanese), plus longitude and latitude:

| column | meaning |
|---|---|
| 時間コード | start of the interval, JST, `YYYYMMDDhhmm` (code 0905 is 09:05–09:09 in the API specification) |
| 常時観測点コード | counter ID |
| 道路種別 | 1 = expressway, 3 = national highway |
| 地方整備局等番号 | regional bureau (81 Hokkaido … 90 Okinawa) |
| 開発建設部／都道府県コード | sub-region (Hokkaido and Chubu only) |
| 上り・小型交通量 / 上り・大型交通量 / 上り・車種判別不能交通量 | vehicles in the interval, "up" direction: small / large / unclassified |
| 上り・停電 / 上り・ループ異常 / 上り・超音波異常 / 上り・欠測 | flags (1 = power failure / loop fault / ultrasonic fault / missing) |
| 下り・… | the same for the "down" direction |
| 経度 / 緯度 | counter location (WGS84) |

## Caveats

- These are counts, not speeds. Congestion has to be inferred, for example by comparing a count with the same weekday and hour.
- The values are reference values, not official MLIT traffic survey results. Some counters are unpublished at times because of faults.
- The API does not give road names. The counter location is the only position information.

## How it is collected

`.github/workflows/collect.yml` runs three times a day. `scripts/collect.py` lists the days the source still holds but the releases do not have, fetches them one request at a time with a pause between requests, and uploads a file when every time code of the day is present (24 hourly or 288 five-minute codes). The source itself lacks a few time codes on many days (for example 287 of 288 five-minute codes; asking again returns nothing), so a day older than yesterday with at most 5% of its codes missing is uploaded as it is. A time code with far fewer counters than the rest of the day counts as missing. A day with a larger gap is retried, and kept as it is only when it is about to leave the source window and still has rows; days about to leave the source are fetched first, then the newest days. A missing time code means the source has no value for it. A missed run is recovered by the next one while the day is still in the source window.

## Source and terms

出典：「交通量 API（国土交通省）機能による交通量(参考値)」を加工して作成
（データ提供：公益財団法人日本道路交通情報センター https://www.jartic-open-traffic.org/ ）

Traffic volume data from the MLIT Traffic Volume API (reference values), provided by the Japan Road Traffic Information Center (JARTIC), processed by this repository. The JARTIC terms state compatibility with CC BY 4.0. This archive is not made or endorsed by MLIT or JARTIC.

Code: MIT License.
