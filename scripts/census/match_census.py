"""Try to tie each counter to the census 2021 section that holds it.

candidates: census sections of the same road type that contain a traffic observation point (観測地点の基本区間番号 =
the section itself), whose observation address geocodes within MAX_KM of the counter. Score by the 24-hour volume:
the counter's September 2026 weekday median against the census 2021 24-hour volume. The large-vehicle share is
not used to match, so it checks the matches."""
import csv, glob, io, json, math, sys
import numpy as np
import pandas as pd

MAX_KM = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
PREF = ("北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 神奈川県 新潟県 富山県 "
        "石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 鳥取県 島根県 "
        "岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県").split()
geo = json.load(open("census/geocode.json", encoding="utf-8"))


def num(x):
    try:
        return float(x)
    except ValueError:
        return np.nan


rows = []
for f in sorted(glob.glob("census/kasyo*.csv")):
    pref = PREF[int(f[-6:-4]) - 1]
    for r in list(csv.reader(io.StringIO(open(f, "rb").read().decode("cp932"))))[1:]:
        if r[3] not in ("1", "3"):
            continue
        for side, base in (("up", 32), ("down", 44)):
            if r[base] != r[0] or not r[base + 1]:
                continue
            a = r[base + 1].replace("　", "").replace(" ", "")
            a = a if a.startswith(pref) else pref + a
            g = geo.get(a)
            if not g:
                continue
            lon, lat = g[0]["geometry"]["coordinates"]
            rows.append(dict(section=r[0], road_type=int(r[3]), route=r[5], addr=a, geo_title=g[0]["properties"]["title"],
                             lon=lon, lat=lat, date=r[base + 5], permanent=r[base + 5].endswith("00"),
                             vol24=num(r[61]), large12_pct=num(r[64]), daynight=num(r[62]),
                             up_pct=100 * num(r[43]) / num(r[61]), lanes=num(r[122]), limit=num(r[140])))
            break
S = pd.DataFrame(rows).drop_duplicates("section")
C = pd.read_csv("repo/data/counters.csv", dtype={"municipality_code": str})
V = pd.read_csv("census/counter_vol.csv")
C = C.merge(V, on=["counter_id", "sensor"], how="inner")
C = C[C.vol24 >= 100]
print(len(S), "census sections with a geocoded observation;", len(C), "counters with a weekday volume")


def km(lat1, lon1, lat2, lon2):
    la1, lo1, la2, lo2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


out = []
for c in C.itertuples():
    s = S[S.road_type == c.road_type]
    d = km(c.latitude, c.longitude, s.lat.values, s.lon.values)
    s = s.assign(km=d)[d <= MAX_KM].copy()
    if s.empty:
        out.append(dict(counter_id=c.counter_id, sensor=c.sensor, n_cand=0))
        continue
    s["lr"] = np.abs(np.log(c.vol24 / s.vol24))
    s = s.sort_values("lr")
    b = s.iloc[0]
    second = s.iloc[1].lr if len(s) > 1 else np.inf
    sec = s.iloc[1] if len(s) > 1 else None
    out.append(dict(counter_id=c.counter_id, sensor=c.sensor, n_cand=len(s), section=b.section, route=b.route,
                    km=b.km, permanent=b.permanent, vol_c=c.vol24, vol_s=b.vol24, lr=b.lr, second_lr=second,
                    large_c=c.large12_pct, large_s=b.large12_pct, nearest_km=s.km.min(),
                    dn_c=c.daynight, dn_s=b.daynight, dn_2nd=sec.daynight if sec is not None else np.nan,
                    up_c=c.up_pct, up_s=b.up_pct, up_2nd=sec.up_pct if sec is not None else np.nan,
                    large_2nd=sec.large12_pct if sec is not None else np.nan,
                    n_perm_cand=int(s.permanent.sum()), best_perm_lr=s[s.permanent].lr.min() if s.permanent.any() else np.nan))
M = pd.DataFrame(out)
M["permanent"] = M.permanent.astype(float)
M.to_csv("census/match_try.csv", index=False)
m = M[M.n_cand > 0]
print("counters with any candidate:", len(m), "of", len(M))
for thr in (0.1, 0.2, 0.3):
    ok = m[(m.lr < thr)]
    gap = ok[ok.second_lr - ok.lr > 0.2]
    dl = (ok.large_c - ok.large_s).abs()
    print(f"|log vol ratio| < {thr}: {len(ok)} ({ok.permanent.mean():.0%} permanent sites), with a clear runner-up gap {len(gap)}; "
          f"large share diff median {dl.median():.1f} pt, 90% {dl.quantile(.9):.1f} pt; km median {ok.km.median():.2f}")
# null for the large share check: same counters, a random candidate of the same road type nationwide
rng = np.random.default_rng(0)
ok = m[m.lr < 0.2]
rs = S[S.road_type == 3].large12_pct.dropna().values
print("null large share diff median", np.median(np.abs(ok.large_c.values[:, None] - rng.choice(rs, (len(ok), 50))).ravel()).round(1))
# stricter null: the runner-up nearby candidate (same counter, same 3 km pool, worse volume fit)
both = ok.dropna(subset=["large_2nd"])
print("nearby runner-up null: n", len(both), "best diff median", (both.large_c - both.large_s).abs().median().round(1),
      "runner-up diff median", (both.large_c - both.large_2nd).abs().median().round(1))
print("by sensor:"); print(m.assign(ok=m.lr < 0.2).groupby("sensor").agg(n=("ok", "size"), ok=("ok", "sum"), perm=("permanent", "mean")))
print("counters with a permanent section within", MAX_KM, "km:", (m.n_perm_cand > 0).sum(), "; its |lr| median", m.best_perm_lr.median().round(2))
for col in ("dn", "up"):
    b2 = ok.dropna(subset=[col + "_2nd"])
    rs = S[S.road_type == 3][{"dn": "daynight", "up": "up_pct"}[col]].dropna().values
    print(col, "best diff median", (b2[col + "_c"] - b2[col + "_s"]).abs().median().round(3), "runner-up", (b2[col + "_c"] - b2[col + "_2nd"]).abs().median().round(3),
          "random", np.median(np.abs(b2[col + "_c"].values[:, None] - rng.choice(rs, (len(b2), 50))).ravel()).round(3))
for p_ in (1.0, 0.0):
    q = ok[ok.permanent == p_]
    print("permanent" if p_ else "one-day", len(q), "large diff", (q.large_c - q.large_s).abs().median().round(2), "daynight diff", (q.dn_c - q.dn_s).abs().median().round(3),
          "up% diff", (q.up_c - q.up_s).abs().median().round(2), "|lr| median", q.lr.median().round(3))
