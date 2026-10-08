"""Frozen rule (2026-10-08, before building the table): a permanent loop counter on a national highway is tied to a
census 2021 section when (1) the one-to-one assignment within 3 km of the geocoded observation address (match_perm.py 3)
pairs them, (2) |log(2026-09 weekday median / census 2021 24h volume)| < 0.2, and (3) either the counter's town name
appears in the census observation address, or both fingerprints agree (up-direction share within 2 pt and day/night
ratio within 0.05). Writes census/census_r3_match.csv.
Amendment after the independent audit (2026-10-08): the peak-hour share check (census col 63, not used by the rule)
showed ties on the town name alone and ties with |lr| >= 0.15 fail far more often (15% and 7 of 26 against 3.7% for
town+fingerprints), so the fingerprints are now required and |lr| must be < 0.15. Having used the peak share to make
this choice, it is no longer an independent check; the up-minus-down large-vehicle share remains one.
Second amendment after the code review (2026-10-08): a pair is dropped when the GSI address search resolved the
observation address only to the prefecture or the municipality (8 pairs: two in Akita geocoded to the prefecture
point, one of them, counter 2110212, tied to a section about 10 km away; six to a city or town point). The
distance to such a point does not place the section, so "within 3 km" would not hold for them."""
import csv, glob, io, json, re
import numpy as np, pandas as pd
PREF = ("北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 神奈川県 新潟県 富山県 "
        "石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 鳥取県 島根県 "
        "岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県").split()
M = pd.read_csv("census/match_perm.csv", dtype={"section": str})
C = pd.read_csv("repo/data/counters.csv", dtype={"municipality_code": str})
town = C[C.sensor == "loop"].set_index("counter_id").town
geo = {re.sub(r"\s", "", k): v for k, v in json.load(open("census/geocode.json", encoding="utf-8")).items()}
# every municipality (MIC list), not only those with a counter; wards appear as e.g. 札幌市中央区
MUN = set(pd.read_csv("repo/data/municipalities_en.csv").municipality.str.replace(r"\s", "", regex=True))
rows = {}
for f in sorted(glob.glob("census/kasyo*.csv")):
    pref = PREF[int(f[-6:-4]) - 1]
    for r in list(csv.reader(io.StringIO(open(f, "rb").read().decode("cp932"))))[1:]:
        if r[3] == "3" and r[0] in set(M.section):
            for base in (32, 44):
                if r[base] == r[0]:
                    rows[r[0]] = (r, r[base + 1].replace("　", "").replace(" ", ""), r[base + 5], pref); break


def num(x):
    try:
        v = float(x)
    except ValueError:
        return np.nan
    return v


def speed(r, flag, col):
    # 計測・非計測の別: keep the value only where the section was measured (flag 1); otherwise blank
    return num(r[col]) if r[flag] == "1" and num(r[col]) > 0 else np.nan


def norm(t):
    t = re.sub(r"^字", "", str(t)); return re.sub(r"[0-9０-９一二三四五六七八九十]+丁目$", "", t)


def coarse(addr, pref):
    # True when the geocode stops at the prefecture or the municipality (title = prefecture [+ district] + municipality)
    a = re.sub(r"\s", "", addr)
    t = geo[a if a.startswith(pref) else pref + a][0]["properties"]["title"]
    rest = re.sub(r"^[^郡市町村]+郡", "", t[len(pref):] if t.startswith(pref) else t)  # drop a leading district (玉名郡)
    return rest == "" or rest in MUN


EN = {"東北中央自動車道": "Tohoku-Chuo Expressway"}  # national highways numbered outside 1-507 in the census
out = []
for m in M[M.lr < 0.15].itertuples():
    r, addr, date, pref = rows[m.section]
    t = norm(town.get(m.counter_id, ""))
    town_ok = len(t) >= 2 and t in addr
    fp_ok = abs(m.up_c - m.up_s) < 2 and abs(m.dn_c - m.dn_s) < 0.05
    if not fp_ok:
        continue
    if coarse(addr, pref):
        print("coarse geocode, dropped:", m.counter_id, addr)
        continue
    out.append(dict(counter_id=m.counter_id, sensor="loop", census_section=m.section.zfill(11), route_number=int(r[4]) if 1 <= int(r[4]) <= 507 else pd.NA,
                    route_ja=r[5], route_en=f"National Route {int(r[4])}" if 1 <= int(r[4]) <= 507 else EN.get(r[5], ""),
                    observation_address_ja=re.sub(r"\s", "", addr), observation_month=f"{date[:4]}-{date[4:6]}",
                    census_vol24_2021=num(r[61]), large12_pct_2021=num(r[64]), lanes=num(r[122]), speed_limit_kmh=num(r[140]),
                    peak_speed_up_kmh=speed(r, 72, 75), peak_speed_down_kmh=speed(r, 79, 82),
                    offpeak_speed_up_kmh=speed(r, 86, 89), offpeak_speed_down_kmh=speed(r, 93, 96),
                    match_km=round(m.km, 2),                     town_in_address=town_ok))
O = pd.DataFrame(out)
# ratio from the stored volumes (lr is unsigned)
v = pd.read_csv("census/counter_vol.csv").query("sensor == 'loop'").set_index("counter_id").vol24
O["match_vol_ratio"] = (O.counter_id.map(v) / O.census_vol24_2021).round(3)
O.to_csv("census/census_r3_match.csv", index=False)
print(len(O), "counters tied; town also in address:", int(O.town_in_address.sum()))
print("route_en blank:", (O.route_en == "").sum(), O[O.route_en == ""].route_ja.tolist())
print("speeds non-null:", O.filter(like="speed").notna().sum().to_dict())
print(O.describe().T[["min", "50%", "max"]].round(2))
print("duplicate sections:", O.census_section.duplicated().sum(), "duplicate counters:", O.counter_id.duplicated().sum())
q = M[M.lr < 0.2]; far = q[(q.up_c - 50).abs() > 3]
print("direction convention check (|up%-50|>3, n", len(far), "): corr same", np.corrcoef(far.up_c, far.up_s)[0, 1].round(3),
      "| sign agree", (np.sign(far.up_c - 50) == np.sign(far.up_s - 50)).mean().round(3))
