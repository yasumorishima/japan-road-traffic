"""Geocode census observation addresses (national highways, sections in municipalities that have a counter)
with the GSI address search; cache in census/geocode.json. Resumable."""
import csv, glob, io, json, os, time, urllib.parse, urllib.request

PREF = ("北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 神奈川県 新潟県 富山県 "
        "石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 鳥取県 島根県 "
        "岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県").split()
C = list(csv.DictReader(open("repo/data/counters.csv", encoding="utf-8")))
mc = {c["municipality_code"] for c in C}
addr = set()
for f in sorted(glob.glob("census/kasyo*.csv")):
    pref = PREF[int(f[-6:-4]) - 1]
    for r in list(csv.reader(io.StringIO(open(f, "rb").read().decode("cp932"))))[1:]:
        if r[3] in ("1", "3") and r[18].zfill(5) in mc:
            for side in (33, 45):
                a = r[side].replace("　", "").replace(" ", "")
                if a:
                    addr.add(a if a.startswith(pref) else pref + a)
cache_p = "census/geocode.json"
cache = json.load(open(cache_p, encoding="utf-8")) if os.path.exists(cache_p) else {}
todo = sorted(a for a in addr if a not in cache)
print(len(addr), "addresses,", len(todo), "to do", flush=True)
for i, a in enumerate(todo):
    url = "https://msearch.gsi.go.jp/address-search/AddressSearch?q=" + urllib.parse.quote(a)
    for k in range(3):
        try:
            cache[a] = json.load(urllib.request.urlopen(url, timeout=30))[:3]
            break
        except Exception as e:
            print("retry", a, e, flush=True)
            time.sleep(5)
    if i % 200 == 0:
        json.dump(cache, open(cache_p, "w", encoding="utf-8"), ensure_ascii=False)
        print(i, flush=True)
    time.sleep(0.5)
json.dump(cache, open(cache_p, "w", encoding="utf-8"), ensure_ascii=False)
print("done", len(cache))
