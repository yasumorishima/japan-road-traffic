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
