"""Does the road distance carry traffic information beyond the straight-line distance? (check, not used to build)
For the 365 census-linked counters: hourly log(vs_baseline) (the counter against its own usual for that hour and
weekday), correlated between every pair of counters within 60 km straight line over the hours both have (>= 300).
Compare pairs at the same straight-line distance that are near by road with pairs that are far or unconnected by road.
usage: graph_validate.py census/road_graph_2021.csv data_dir"""
import sys, glob
import numpy as np, pandas as pd
import pyarrow.parquet as pq
pairs_f, ddir = sys.argv[1], sys.argv[2]
C = pd.read_csv("repo/data/census_2021_counters.csv")
P = pd.read_csv("repo/data/counters.csv").set_index("counter_id")
ids = C.counter_id.tolist()
frames = []
for f in sorted(glob.glob(f"{ddir}/hourly_*.parquet")):
    t = pq.read_table(f, columns=["time_jst", "counter_id", "vs_baseline"], filters=[("counter_id", "in", ids)]).to_pandas()
    frames.append(t)
H = pd.concat(frames)
H = H[H.vs_baseline > 0]
H["z"] = np.log(H.vs_baseline)
M = H.pivot_table(index="time_jst", columns="counter_id", values="z")
print("hours", len(M), "counters with data", M.shape[1])
X = M.astype("float64").to_numpy(dtype="float64", na_value=np.nan); ok = ~np.isnan(X); X0 = np.where(ok, X, 0.0)
cols = list(M.columns); pos = {c: i for i, c in enumerate(cols)}
lat = np.radians(P.loc[cols, "latitude"].to_numpy()); lon = np.radians(P.loc[cols, "longitude"].to_numpy())
R = pd.read_csv(pairs_f)
road = {(a, b): d for a, b, d in zip(R.counter_a, R.counter_b, R.road_km)}
out = []
for i in range(len(cols)):
    for j in range(i + 1, len(cols)):
        h = np.sin((lat[j] - lat[i]) / 2) ** 2 + np.cos(lat[i]) * np.cos(lat[j]) * np.sin((lon[j] - lon[i]) / 2) ** 2
        line = 2 * 6371 * np.arcsin(np.sqrt(h))
        if line > 60:
            continue
        both = ok[:, i] & ok[:, j]
        if both.sum() < 300:
            continue
        r = np.corrcoef(X[both, i], X[both, j])[0, 1]
        a, b = cols[i], cols[j]
        d = road.get((a, b), road.get((b, a), np.inf))
        out.append((a, b, line, d, r, both.sum()))
O = pd.DataFrame(out, columns=["a", "b", "line", "road", "r", "n"])
print("pairs", len(O), "with a road path <= 60 km", np.isfinite(O.road).sum())
O["lb"] = pd.cut(O.line, [0, 5, 10, 20, 40, 60])
O["rr"] = np.where(~np.isfinite(O.road), "none<=60", np.where(O.road / O.line < 1.5, "direct(<1.5x)", "detour(>=1.5x)"))
print(O.pivot_table(index="lb", columns="rr", values="r", aggfunc=["median", "count"], observed=True).round(3).to_string())
# within straight-line bands, does a shorter road distance go with a higher correlation?
import scipy.stats as st
C2 = C.set_index("counter_id").route_ja
O["same"] = C2.loc[O.a].to_numpy() == C2.loc[O.b].to_numpy()
# all pairs (a pair with no road path within 60 km counts as 120 km, so far bands mostly measure "reachable or not"),
# then only pairs with a road path, then pairs on different routes
for label, sel in (("all", O.index == O.index), ("with a road path", np.isfinite(O.road)),
                   ("different routes", ~O.same & np.isfinite(O.road))):
    for lo, hi in ((0, 10), (10, 20), (20, 40), (40, 60)):
        q = O[sel & (O.line > lo) & (O.line <= hi)].copy()
        q["road_c"] = np.minimum(q.road, 120.0)
        print(f"{label:17s} line {lo}-{hi} km: n={len(q)} spearman(r, road)={st.spearmanr(q.r, q.road_c).statistic:.3f}"
              f" spearman(r, line)={st.spearmanr(q.r, q.line).statistic:.3f}")
O.to_csv("census/graph_validate_pairs.csv", index=False)
