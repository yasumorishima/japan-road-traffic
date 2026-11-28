# CLAUDE.md

このリポジトリで作業する Claude Code 向けのメモ。運用の仕組み、クラウドセッションでできること・できないこと、障害対応の手順と過去の経緯を残す。新しく分かったことはここに追記する。

## 全体像

国交省の交通量 API を毎日集めて GitHub Releases に保存し、Kaggle データセット `yasunorim/japan-road-traffic-volume` として公開する。詳しい中身は README.md。

| ワークフロー | 起動 | 役割 |
|---|---|---|
| `collect.yml`（Collect traffic volume） | cron 18:17 / 01:47 / 09:23 UTC | 交通量を `raw-*` リリースへ |
| `publish.yml`（Publish to Kaggle） | collect の完了後 / 手動 / `kaggle/publish-request` の push | Kaggle 用ファイルを作って新バージョンを公開 |
| `collect-weather.yml` | cron 1日3回 | AMeDAS を `amedas-YYYYMM` リリースへ |
| `collect-police.yml` | cron 01:41 / 07:41 / 13:41 / 19:41 UTC | 警察の断面交通量を1回1か月ずつ `police-YYYY` リリースへ |
| `build-check.yml` | main 以外のブランチへの push（`scripts/**`, `data/**`） | テスト、仕込んだ欠陥の検出、Kaggle ファイルの全ビルド（公開はしない） |

- GitHub の cron はよく遅れ、ときどき実行自体が飛ばされる（2026-10-28 の 01:41 UTC の police は起動しなかった）。queued のまま1時間以上待つこともある。コードの問題と取り違えないこと。
- 公開済みかどうかは `data/published_assets.txt` で判断する。公開が失敗するとこのファイルは更新されないので、次の定期実行が自動でやり直す。

## クラウドセッション（Claude Code on the web）でできること・できないこと

- できる：ブランチへの push、PR の作成とマージ（GitHub MCP）、Actions のログ取得、公開 API の参照（`curl https://api.github.com/...` は認証なしで読める。公開リポジトリのため）。
- できない：
  - `workflow_dispatch`（手動実行）。403 になる。Claude GitHub App に Actions の書き込み権限がない。ユーザー側でも追加できない。
  - 失敗した実行の Re-run。そもそも、失敗時の古いワークフロー定義で動くので役に立たない。
  - リリースアセットの削除。
- 代わりの手段（どちらも push でき、PR をマージするだけで動く）：
  - **Kaggle を今すぐ公開したい**：`kaggle/publish-request` に理由を1行足した PR を main にマージする。`publish=true` の手動実行と同じ動きになる。
  - **失敗した警察データの月をもう一度試したい**：`data/police_retry.txt` に `YYYY-MM 理由` を書いた PR をマージする。`police_failed_*` の印は無視されるが、3回までの試行制限は残る。取り込めたらその行を消す PR を出す。
- 元データのサーバー `storage.compusophia.com:1475` にはクラウドセッションから接続できない（タイムアウト）。中身を調べるときは、作業ブランチに一時的なワークフローを置き、GitHub 上で HTTP Range 読みをする（2026-10-27 に月 4.2 GB のうち 12 MB だけ読んで秋田の中身を一覧にした）。調べ終わったら一時ファイルは消し、main に入れない。
- Kaggle の状態は認証なしで見られる：`curl https://www.kaggle.com/api/v1/datasets/view/yasunorim/japan-road-traffic-volume`（`versions[].status` に Ready / Failed が出る）。Kaggle CLI もプロキシ経由で動く（`KAGGLE_USERNAME=yasunorim KAGGLE_KEY=dummy` を設定すればよいが、ときどき認証エラーになる）。
- PR はユーザーの了承を得て、Claude がマージまで行う運用。`build-check` が動く変更は、それが通ってからマージする。

## 手元での確認

- 交通量側：`python scripts/test_fetch.py`, `test_collect.py`, `test_fixtures.py`, `test_enrich.py` など（`publish.yml` の Install and test と同じ）。
- 警察側：`lhasa` と、`polars==1.34.0 pyarrow==21.0.0 pandas==2.3.3 numpy==2.4.6` を入れた別の venv が必要（`build-check.yml` と同じ）。
  - `cd scripts/police && python test_police.py`
  - `python scripts/police/mutate_police.py` は、コードをわざと壊してテストが見抜けるかを確かめる。結果は `N planted defects, 0 not caught` でなければならない。
  - コードを書き換えると、仕込んだ欠陥の置換元が見つからず `MUTATION DID NOT APPLY` になる。新しいコードに合わせて欠陥も足すか直すこと。build-check はこれで落ちる。
- Python パッケージのバージョンはワークフローごとに固定している。勝手に上げない。
- 固定していない間接依存が新版で壊れることがある。kaggle 2.2.4 は `six` を宣言せずに使うため、`python-dateutil` を 2.9.0.post0 に固定している（4.0.0 で `six` が依存から外れた）。

## 規約

- コード・コメント・コミットメッセージは英語。
- 警察データは、変換（`convert.py`）と独立した検算（`verify.py`、コードを共有しない）が一致したときだけアップロードする。入力の扱いを変えるときは両方を別々に直し、テストと仕込んだ欠陥も足す。
- 想定外の入力は止めるのが基本。読める形を増やすときは条件をできるだけ狭くする。

## 評価の作法（2026-11 追記）

Anthropic の記事「Building strong safety evaluations」（2026-11-13）から、このリポジトリの予測・検証に当てはまるものを自分たちの言葉でまとめた。既に上に書いてあること（推測で直さず実物を見る、想定外の入力は止める）は繰り返さない。

- **何を測るかを先に書く。** 指標を選ぶ前に「どの日の、どの計数器の、何台を当てたいか」を1文で書く（例：前夜までに分かる情報で翌日の台数、WAPE）。指標が問いに合っていなければ、点が良くても意味がない。
- **何もしない腕を最初に測る。** forecasting starter では `baseline_vehicles`（同じ計数器・時刻・曜日の直前4回の中央値）と、前週同曜日（`lag7`）を先に出し、モデルはそれを上回った分だけを成果とする。新しい特徴やモデルを足すときも、まずこの2本との差を見る。
- **偽の腕（プラシーボ）で仕掛けを確かめる。** Task 2 の「無関係な計数器の隣」のように、効くはずのない情報を同じ形で入れた腕を置く。本物との差がプラシーボとの差より小さければ、効いているのは中身ではなく形（全国一斉の揺れなど）。
- **条件の次元は独立に組む。** 日の種類（平日・週末・連休）と地域・道路の種類・時間帯を掛け合わせて見る。「連休は全部関東」のように2つの次元が重なると、どちらが効いたのか分からなくなる。
- **未来の値を特徴に入れない。** 特徴は予測する時点より前の値だけから作る（`baseline_vehicles` が前の日だけを使うのはそのため）。分割は時間で切り、試験期間の値が学習側や基準値に混ざらないことを確かめる。
- **驚く点が出たら、まず仕掛けを疑う。** 急に良くなった・悪くなったときは、モデルより先に結合のずれ、シフトの向き、欠けた行の扱い、試験期間の切り方を確かめる。
- **同じ項目で対にして比べ、区間を付ける。** 2つの腕は同じ（計数器・日）の誤差の差を取り、その平均に区間（計数器ごとのブロック・ブートストラップなど）を付ける。全体の平均どうしを並べるだけでは、差が雑音かどうか分からない。
- **区分ごとに出し、伸びなかった区分も並べる。** 平日・休日、地域、時間帯、道路の種類ごとに出す。starter の「平日は基準値とほぼ同じ、伸びたのは休日」のように、変わらなかった区分も消さずに残す。区分を多く見るほど偶然の差が出やすいので、事前に決めた区分を主に読む。
- **設定を全部書き、違う設定の点は比べない。** データの版（Kaggle の version、期間）、試験期間、特徴、ハイパーパラメータ、乱数の種を結果と一緒に残す。2つ以上の設定が違う点どうしは比べない。
- **落とした行は数を出す。** 故障フラグや欠けた時間の行を黙って落とさない。警察データの manifest は落とした行を種類と地域ごとに数えており（README）、`daily_summary.csv` は24時間そろわない日の合計を空にして `stopped` を別に数えている。評価でも、除いた行の数と理由を結果に添える。

## 過去の障害と対応

- **2026-10-24〜27 Kaggle 公開が毎回失敗（publish #66〜#73）**
  - 原因：kaggle CLI 2.2.5 が出たため、固定している 2.2.4 が「古いバージョン」の警告を標準出力に出すようになった。JSON の前に警告が混ざって `jq` が失敗し、既存のデータセットを「無い」と誤判定して新規作成に進み、タイトル重複で失敗していた。
  - 対応：#15 で JSON の行だけ取り出す `status_json` を追加した。#16 で `kaggle/publish-request` の仕組みを追加した。
- **2026-10-27 Kaggle v28 が Kaggle 側で Failed（publish #76）**
  - アップロードは成功したが、Kaggle が理由を出さずに Failed にした。ファイル構成は v27 とほぼ同じで、一時的な失敗と判断した。
  - #19 で publish-request を push して v29 が Ready になった。同じことが起きたら、まず `versions[].status` を確認してやり直す。
- **2026-10-27〜28 警察データ 2023-06 秋田で停止（police #42, #43）**
  - `typeB_akita_2023_06.zip` の中身は、フォルダと CSV、それに同じフォルダを固めた `typeB_akita_2023_06.zip`（同じ CSV がもう1つ）だった。
  - #17 では「中に ZIP が1つだけ」と思い込んで外した。#18 で、自分と同じ名前の ZIP の CSV も1段だけ読むようにし、同じ内容の CSV は1回だけ数えるようにした。
  - #44 で取り込めた（103万行、161地点）。#20 で retry リストから外した。
  - 教訓：推測で直さず、まず実物の中身を見る。
- **2026-11-01 Kaggle 公開が kaggle CLI の起動で失敗（publish #92）**
  - 原因：同日 07:02 UTC に `python-dateutil` 4.0.0 が出て、依存から `six` が消えた。kaggle 2.2.4 は `six` を宣言しないまま import しているため、`ModuleNotFoundError: No module named 'six'` で落ちた（「dataset does not exist」の表示は、status 取得が失敗したことによる副作用）。
  - 対応：#22 で `python-dateutil==2.9.0.post0` を固定した。続く 01:47 UTC の collect が GitHub に飛ばされたため、#23 で publish-request を push して公開した。
- **2026-11-06 Kaggle 公開が GitHub のアセット取得で失敗（publish #108）**
  - 原因：`gh release download` の途中で、GitHub がアセット1件（`raw-202607/loop_1h_road3_20260708.csv.gz`）に HTTP 500 を返した。ファイル自体は壊れておらず、直後に取得すると 200 だった。GitHub 側の一時的なエラー。
  - 対応：#24 で、リリースのダウンロードを1タグにつき最大3回（30秒おき、取得済みのファイルは飛ばす）再試行するようにした。公開が失敗しても `data/published_assets.txt` は更新されないので、次の collect 後の publish が自動でやり直す。
- **2026-11-26〜27 警察データ 2017-12 三重と 2017-10 大阪で停止（police #112, #114）**
  - 2017-12：`三重県警_201712.csv` に見出し行がなく、1行目がデータだった（`unknown column '2017/12/1 0:00:00'`）。列の並びは 2017-11 三重の見出し（9列）と同じだった。
  - 2017-10：大阪の `大阪府警_20171001-15.csv` で、10-12 11:00〜10-13 10:50 のほぼ全地点・全時刻が2行ずつあり、数が違った（464,394 キー、928,788 行、門の 0.1% を超えて約 6%）。2行のうち1行は隣の時刻の値の繰り返しで、どちらが正しいか決められない。
  - 調べ方：作業ブランチに一時ワークフローを置き、HTTP Range で月の ZIP から該当地域の ZIP だけを取り出して中身を見た。調べ終わって消した。
  - 対応：#27 で、三重のこのファイルだけ名前を指定して見出しを補う（`NO_HEADER`、検算側も別に `HEADERLESS` を持つ）。1行目が同じ列数のデータ行でなければ止まる。大阪はこの地域・月の conflict_rows だけ 928,788 行まで認める（`GATE_ALLOW`）。食い違う時刻は今までどおり落として数える。`data/police_retry.txt` に2か月を入れた。取り込めたら行を消す。
  - 2017-12 は police #115 で取り込めた（3.19 億行、28,249 地点、三重は `header_supplied`）。retry リストから外した。2017-10 は次の実行で試す。
  - 教訓：門で止まったら、まず実物を見て、入力の誤りか読み方の誤りかを分ける。入力の誤りと分かったら、名前を指定した狭い例外にし、門そのものは緩めない。
  - 調査用スクリプトを `inspect.py` と名付けたら標準ライブラリの `inspect` とぶつかり、numpy の import で落ちた。一時スクリプトに標準モジュールと同じ名前を付けない。

## Progress log

### 2026-11-28 Rust を足す価値がある場所を測った（結論：今は入れない）

参考：Anthropic「Migrating our API to Rust」（2026-11-25）。そこから借りたのは、今の実装を正解とし、段ごとに出力を比べ、最初にずれた段と、ずれの形だけを記録するやり方。

**1. 直近 10 回の成功 run（中央値／最大、秒）と、時間の中身**

| ワークフロー・手順 | 中央値 | 最大 | 中身 |
|---|---|---|---|
| police：Fetch, convert, check and upload | 1168 | 1391 | ダウンロード 390〜595 s（compusophia）＋変換 367〜729 s＋検算 38〜122 s＋アップロード 14〜21 s。`/usr/bin/time` で CPU は 1.1〜1.5 コア分（4 コア中） |
| police：Tests (offline) | 19 | 29 | |
| collect：Fetch missing days | 300 | 639 | 交通量 API の待ち。1 要求あたり wall 3〜4 s に対し CPU 0.01〜0.03 s（`PAUSE` 2 s を含む） |
| weather：Fetch missing days | 2 | 31 | ほぼ空振り。最長 15 分の run は、ランナー割り当ての待ちが 14 分で、処理は 7 s |
| publish：Download + build | 0（空振り）| 290 | 下の build_kaggle がほとんど |
| publish：Publish a Kaggle version | 0 | 134 | Kaggle へのアップロード |
| build-check：Full build | 293 | 331 | リリースの取得と build_kaggle、検算 |
| build-check：police tests + planted defects | 134 | 143 | テストの繰り返し |

参考の最長（直近 30 回）：police 146 分の run（#94, 2019-06）は、ダウンロードに約 2 時間かかっていた（CPU 24%）。これは CPU ではなく通信が原因。

**2. Python の CPU 計算を段ごとに測った（手元の 4 コアの機械。CI と同じ固定バージョン）**

build_kaggle.py。リリース 827 ファイル（181 MB）から 36 ファイルを作るのに 190 s かかる。
- read（CSV.gz → 型付きの表）：105 s
- derive：18 s
- baseline（直前 4 日の中央値）：18 s
- add_weather：15 s
- concat・sort・重複チェック：12 s
- to_parquet：8 s
- load_weather：6.5 s
- daily_summary：5.4 s

このほか build_counters は 35 s、verify_enrich は 36 s、verify_daily は 7 s かかる。read が遅い理由：
- `set(df[c])` が Arrow の文字列列を 1 セルずつ Python オブジェクトにする（7,800 万回、38 s）。
- 文字列への `pd.to_numeric`（24 s）。
- `read_csv(dtype=str)`（26 s）。

police convert.py。公開済みの 2017-12 出力から元の形の CSV（CP932、地域 ZIP の入った月 ZIP）を作り直した。神奈川・東京・大阪の 5,530 万行。
- 合計 110 s（0.5 M 行/s で、CI の 0.46〜0.85 M 行/s と同じくらい）
- convert_region：80 s
- to_utf8（iconv）：13 s
- ZIP 展開：8 s
- Parquet 書き出し：5 s

convert_region の中身：polars の遅延フレームが、地域ごとに同じ CSV を 12 回、最初から読み直している（統計 1 回、属性 6 回、コード 1 回、本体 1 回）。CSV を 1 回読むのに 2.8 s かかる。

**3〜4. 候補ごとの比較（出力はすべて今の Python 版と比べた）**

| 候補 | 今 | Python のまま縮めた実測 | Rust 試作（PyO3）の実測 | 判定 |
|---|---|---|---|---|
| build_kaggle の read（827 ファイル） | 104 s | 38.5 s（`set(...unique())`、pyarrow.csv で一括読み、数字列だけ pyarrow で int に変換し、それ以外は今のコードへ戻す） | 29.0 s（flate2+csv で 1 回読み、整数と文字列のバッファを返す） | **入れない**。Rust で上乗せできるのは 9.5 s だけ |
| build_kaggle 全体 | 190 s | 118 s | 112 s | **入れない**。差は 6 s で、publish は 1 日 1〜3 回 |
| police の 1 回分の統計パス（神奈川 2,050 万行） | 3.5 s（polars、CSV） | 0.74 s（一度 IPC に書いてから読む） | 5.6 s（1 スレッドの素朴な実装。polars より遅い） | **入れない**。polars はもともと Rust なので、無駄は読み直しの回数にある |
| police convert_region（神奈川） | 29.4 s | 13.6 s（CSV を一度だけ `sink_ipc` してから同じ convert_region を実行。five・points・stats が完全に一致） | 試作なし（下の理由） | **入れない** |
| collect／weather の取得、police のダウンロード、Kaggle への公開 | 300〜1200 s | 対象外 | 対象外 | 通信が主なので Rust の対象にしない |

一致の確かめ方：
- read は 827 ファイルすべてで、列・dtype・値が一致した（`DataFrame.equals`）。
- Python 版と Rust 版の両方で build を最後まで走らせ、36 個の出力（Parquet・CSV）がバイト単位で同じだった。
- 比較スクリプトは、ずれたときに最初のファイル・列・行を出す。わざと 1 セル変えた出力で `hourly_2026-10_11-20.parquet: column vehicles row 12345: 1507 vs 1508` と出ることを確かめた。

police の Rust 試作は、合成データに不正な行がなく、数が一致してもあまり意味がない。

**判断の理由**
- CPU が支配的なのは build_kaggle（publish・build-check）と police の変換だけだった。どちらも主な無駄は Python 側の書き方で、pandas/pyarrow/polars のまとめ処理で縮む。
  - build_kaggle は、1 セルずつの処理と、文字列の数値化。
  - police は、同じ CSV を 12 回読み直していること。
- その上に Rust を足しても、伸びは小さい。
  - build_kaggle で 6 s。
  - police では polars のほうが速い。
- 一方で Rust を足すと、次の負担が増える。
  - 2 つ目の実装と、その一致の確かめ。
  - CI での Rust のビルド（試作は依存のキャッシュありで 12 s）。
  - 仕込んだ欠陥の試験を 2 つの実装に保つこと。
- 通信の待ち（police のダウンロード、交通量 API、Kaggle）が wall の半分以上を占める。
- 時間切れの心配はない。いずれも上限よりずっと短い。
  - collect は最長 11 分で、上限 300 分（元データの保持は 5 分値で 28 日、1 時間値で 88 日）。
  - weather は最長 15 分で、上限 60 分（気象庁の保持は約 9 日、1 日 3 回取得）。
  - police は最長 146 分で、上限 300 分。
- 取得の別案（並列化など）は、今は要らない。police のダウンロードが 2 時間を超えることが続くなら、そのときに考える。

**次にやるなら（Rust ではなく Python のまま。今回は main に入れていない）**
- build_kaggle の read を上の形に直す：190 s → 約 118 s。publish と build-check が 1 回あたり約 70 s 短くなる。直すときは、今の read と全ファイルで `equals` を取り、出力をバイト比較する。数字列以外の値は、今のコードへ戻す。
- police の convert_region の前で、地域の CSV を一度だけ型付きの IPC に書く：変換 370〜730 s → 推定 170〜330 s。
  - 一時ファイルが要る。神奈川で数 GB になるので、ディスクを確かめる。
  - 変換と検算を両方そのまま通し、仕込んだ欠陥も足す。
- 測り直す手順：
  - 手順の時間は `actions/runs/{id}/jobs` の steps から取る。
  - police の内訳はログの `downloaded … in N s`、`region …`、`totals`、`recomputed independently`、`uploaded to` の時刻と、`/usr/bin/time` の CPU 時間から取る。
  - build_kaggle は、リリースを取ってきて段ごとにタイマーを付けて実行する。
