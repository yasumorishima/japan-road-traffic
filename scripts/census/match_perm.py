"""One-to-one matching of loop counters on national highways to census 2021 permanent ('YYYYMM00') sections.
cost = |log volume ratio| (2026-09 weekday median vs census 2021 24h); only pairs within R km of the geocoded
observation address. Checks not used in matching: day/night ratio, up-direction share, large-vehicle share.
Null: same counters assigned to their best permanent section among those NOT chosen (runner-up)."""
import sys
import numpy as np, pandas as pd
from scipy.optimize import linear_sum_assignment
exec(open("match_census.py", encoding="utf-8").read().split("def km(")[0])  # builds S, C
R = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0


def km(lat1, lon1, lat2, lon2):
    la1, lo1, la2, lo2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


P = S[(S.road_type == 3) & S.permanent].reset_index(drop=True)
L = C[(C.road_type == 3) & (C.sensor == "loop")].reset_index(drop=True)
D = km(L.latitude.values[:, None], L.longitude.values[:, None], P.lat.values[None], P.lon.values[None])
LR = np.abs(np.log(L.vol24.values[:, None] / P.vol24.values[None]))
BIG = 1e6
cost = np.where(D <= R, LR, BIG)
ri, ci = linear_sum_assignment(cost)
keep = cost[ri, ci] < BIG
ri, ci = ri[keep], ci[keep]
M = pd.DataFrame(dict(counter_id=L.counter_id.values[ri], section=P.section.values[ci], km=D[ri, ci], lr=LR[ri, ci],
                      large_c=L.large12_pct.values[ri], large_s=P.large12_pct.values[ci], dn_c=L.daynight.values[ri],
                      dn_s=P.daynight.values[ci], up_c=L.up_pct.values[ri], up_s=P.up_pct.values[ci]))
# runner-up: best other permanent section within R for the same counter
c2 = cost.copy(); c2[ri, ci] = BIG
j2 = c2[ri].argmin(1); has2 = c2[ri, j2] < BIG
M["lr2"] = np.where(has2, c2[ri, j2], np.nan)
for a, b in (("large", "large12_pct"), ("dn", "daynight"), ("up", "up_pct")):
    M[a + "_2"] = np.where(has2, P[b].values[j2], np.nan)
print(len(P), "permanent sections geocoded;", len(L), "loop counters on national highways; matched", len(M), "within", R, "km")
for thr in (0.1, 0.2, 0.3):
    q = M[M.lr < thr]; q2 = q[q.lr2.notna()]
    agree = lambda d, u, n: (((d.up_c - d[u]).abs() < 2) & ((d.dn_c - d[n]).abs() < 0.05)).mean()
    print(f"lr<{thr}: {len(q)}  km med {q.km.median():.2f}  | diffs best/runner-up: large {(q2.large_c-q2.large_s).abs().median():.2f}/{(q2.large_c-q2.large_2).abs().median():.2f}"
          f"  daynight {(q2.dn_c-q2.dn_s).abs().median():.3f}/{(q2.dn_c-q2.dn_2).abs().median():.3f}  up% {(q2.up_c-q2.up_s).abs().median():.2f}/{(q2.up_c-q2.up_2).abs().median():.2f}"
          f"  both-fingerprints agree {agree(q2,'up_s','dn_s'):.2f}/{agree(q2,'up_2','dn_2'):.2f} (n2={len(q2)})")
print("km distribution of lr<0.2:", M[M.lr < 0.2].km.quantile([.5, .8, .95]).round(2).tolist())
M.to_csv("census/match_perm.csv", index=False)
