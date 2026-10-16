"""Road distances between the census-linked loop counters (2026-10-17). Writes census/road_graph_2021.csv and
census/census_r3_placed.csv (census_r3_match.csv plus where on the census road network each counter was placed).

The network is every section of the 2021 census 箇所別基本表 (all road types: expressways, national highways, prefectural
and designated-city roads; 99,417 sections) except the sections the census marks impassable to vehicles (区間種別 6,
交通不能区間). Each section has a start and an end point, and the section length (column 23, km, 0.1 km steps; 0.0 means
shorter than 0.05 km). Points are merged when the census says they are the same place (connection codes,
kasyorep.pdf (4)):
  A  consecutive section numbers of one route in one prefecture (first 7 digits) whose end and start descriptions agree:
     the same connection code and the same connected section (codes 1-4), the same pair of municipalities (6), or the
     same name (5, 7, 9); never across a dead end (8);
  J  code 1 (another branch's section END is at this point) or 2 (another branch's START is here) naming a section in
     the table;
  B  code 3 (prefecture border) naming the same route's section across the border, joined to the side of that section
     that names this one back.
Checked and not used: also joining consecutive sections whose connection codes agree but name different sections of
one other route (an interchange spread over two sections) made 179 counter pairs geometrically impossible instead of 3.
A first draft on national highways alone left many counter pairs without a path (two national routes are often joined
only through a prefectural road), so all road types are used.

Where a counter sits: the census values of a section are those of its survey unit. Every section names the observation
section of its unit (column 32), and census_section in census_2021_counters.csv is that observation section, but the
observation point can be in any section of the unit (route 29 in Hyogo: unit 0100 runs over sections 0100-0190 across three
cities; its address, 宍粟市山崎町須賀沢, is in 0180-0190). A counter is placed at the middle of a stretch: the sections of
its unit that lie in the counter's own municipality (column 18) joined end to start, the stretch holding the observation
section when there is one, else the longest. When no section of the unit is in the counter's municipality (a counter
near a municipal border), all sections of the unit are used and placed_in_municipality is False. placed_km is the length
of the stretch.
road_km between two counters is the shortest path between the middles of their stretches: it may leave a stretch at any
junction along it (the distance from the middle to that junction along the stretch, then the network).

Checks (printed; the run stops if they fail): a straight line cannot be longer than the road, so line_km must be at most
road_km + half of both placed_km + 0.5 km; more than 1% failing stops the run.
direct: no third counter lies on the path (d(a,c) + d(c,b) <= d(a,b) + 0.125 km for none: distances move in 0.05 km steps, so
this is "within 0.1 km" and no sum sits on the threshold), the edges a graph model would
usually take. graph_validate.py is a check not used to build.
usage (from the work directory): build_graph.py [max_road_km=60]"""
import csv, glob, io, sys, collections
import numpy as np, pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
MAXKM = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
rows = {}
for f in sorted(glob.glob("census/kasyo*.csv")):
    for r in list(csv.reader(io.StringIO(open(f, "rb").read().decode("cp932"))))[1:]:
        for c in (0, 7, 12, 32):
            r[c] = r[c].zfill(11) if r[c] else ""
        rows[r[0]] = r
assert len(glob.glob("census/kasyo*.csv")) == 47 and len(rows) == 99417, len(rows)
secs = sorted(rows); idx = {s: i for i, s in enumerate(secs)}; n = len(secs)
L = np.array([float(rows[s][23]) for s in secs])
passable = np.array([rows[s][20] != "6" for s in secs])
print("impassable sections (区間種別 6) left out as edges:", int((~passable).sum()))
parent = list(range(2 * n))  # point id: 2*i start, 2*i+1 end
S_, E_ = 0, 1
cnt = collections.Counter()


def find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]; x = parent[x]
    return x


def union(a, b, tag):
    ra, rb = find(a), find(b)
    if ra != rb:
        parent[ra] = rb; cnt[tag] += 1


def pt(s, side): return 2 * idx[s] + side


def key(r, side):
    c, x, nm = (r[6], r[7], r[9]) if side == S_ else (r[11], r[12], r[14])
    if c in ("1", "2", "3", "4"):
        return (c, x)
    if c == "6":
        return (c, frozenset(nm.replace("境", "").split("・")))
    return (c, nm)


groups = collections.defaultdict(list)
for s in secs:
    groups[s[:7]].append(s)
for ss in groups.values():
    for a, b in zip(ss, ss[1:]):
        ka, kb = key(rows[a], E_), key(rows[b], S_)
        if ka == kb and ka[0] != "8" and (ka[1] or ka[0] in ("5", "9")):
            union(pt(a, E_), pt(b, S_), "A")
for s in secs:
    r = rows[s]
    for side, c, x in ((S_, r[6], r[7]), (E_, r[11], r[12])):
        if c in ("1", "2") and x in idx:
            union(pt(s, side), pt(x, E_ if c == "1" else S_), "J")
        if c == "3" and x in idx:
            o = rows[x]
            back = [sd for sd, cc, xx in ((S_, o[6], o[7]), (E_, o[11], o[12])) if cc == "3" and xx == s]
            if len(back) == 1:
                union(pt(s, side), pt(x, back[0]), "B")
print("merges", dict(cnt))
roots = sorted({find(p) for p in range(2 * n)}); rid = {r: i for i, r in enumerate(roots)}
u = np.array([rid[find(2 * i)] for i in range(n)]); v = np.array([rid[find(2 * i + 1)] for i in range(n)])
# Parallel sections between the same two points (a bypass and the old road, divided carriageways) are one edge with
# the shorter length: a sparse matrix built from duplicate entries would add their lengths.
E = pd.DataFrame({"a": np.minimum(u, v)[passable], "b": np.maximum(u, v)[passable], "L": L[passable]})
E = E[E.a != E.b].groupby(["a", "b"], as_index=False).L.min()  # a section whose two ends are one point is no path
print("edges", len(E), "from", int(passable.sum()), "passable sections")
W = coo_matrix((np.r_[E.L, E.L], (np.r_[E.a, E.b], np.r_[E.b, E.a])), shape=(len(roots), len(roots))).tocsr()
assert W.nnz <= 2 * len(E) and np.isclose(W.sum(), 2 * E.L.sum())  # no entry summed with another

C = pd.read_csv("census/census_r3_match.csv", dtype={"census_section": str})
P = pd.read_csv("repo/data/counters.csv", dtype={"municipality_code": str}).set_index("counter_id")
assert C.census_section.isin(idx).all() and C.counter_id.isin(P.index).all()
unit = collections.defaultdict(list)
for s in secs:
    if rows[s][32]:
        unit[rows[s][32]].append(s)
nodes, offs, first, last, plen, inmun, stat = [], [], [], [], [], [], collections.Counter()
for c in C.itertuples():
    mem = sorted(unit.get(c.census_section, [c.census_section]))
    mun = P.municipality_code[c.counter_id]
    inm = [s for s in mem if rows[s][18].zfill(5) == mun]
    run = inm or mem
    stat["unit of 2+ sections"] += len(mem) > 1; stat["no section in the counter's municipality"] += not inm
    chains, cur = [], [run[0]]
    for a, b in zip(run, run[1:]):
        if find(pt(a, E_)) == find(pt(b, S_)):
            cur.append(b)
        else:
            chains.append(cur); cur = [b]
    chains.append(cur)
    own = [q for q in chains if c.census_section in q]
    ch = own[0] if own else max(chains, key=lambda q: sum(L[idx[s]] for s in q))
    stat["stretch not joined end to start"] += len(chains) > 1
    pos = np.r_[0.0, np.cumsum([L[idx[s]] for s in ch])]  # chain points: start of ch[0], then the end of each section
    nodes.append([rid[find(pt(ch[0], S_))]] + [rid[find(pt(s, E_))] for s in ch])
    offs.append(np.abs(pos - pos[-1] / 2))
    first.append(ch[0]); last.append(ch[-1]); plen.append(pos[-1]); inmun.append(bool(inm))
print("placement", dict(stat))
C["placed_from_section"] = first; C["placed_to_section"] = last; C["placed_km"] = np.round(plen, 1)
C["placed_in_municipality"] = inmun
targets = np.unique(np.concatenate([np.array(q) for q in nodes])); tcol = {p: k for k, p in enumerate(targets)}
DT = np.full((len(targets), len(targets)), np.inf)
for lo in range(0, len(targets), 100):  # distances among chain points only, in batches (the full rows would not fit)
    D = dijkstra(W, directed=False, indices=targets[lo:lo + 100], limit=MAXKM + 30)
    DT[lo:lo + 100] = D[:, targets]
k = len(C)
R = np.full((k, k), np.inf)
for i in range(k):
    ti = np.array([tcol[p] for p in nodes[i]])
    for j in range(k):
        if i == j:
            R[i, j] = 0.0; continue
        tj = np.array([tcol[p] for p in nodes[j]])
        R[i, j] = (offs[i][:, None] + DT[np.ix_(ti, tj)] + offs[j][None, :]).min()
near = (R <= MAXKM) | (R.T <= MAXKM)  # the search stops at MAXKM + 30, so only distances beyond it may differ by direction
assert np.allclose(R[near], R.T[near])
lat = np.radians(P.loc[C.counter_id, "latitude"].to_numpy()); lon = np.radians(P.loc[C.counter_id, "longitude"].to_numpy())
out = []
for i in range(k):
    for j in range(i + 1, k):
        if R[i, j] > MAXKM:
            continue
        h = np.sin((lat[j] - lat[i]) / 2) ** 2 + np.cos(lat[i]) * np.cos(lat[j]) * np.sin((lon[j] - lon[i]) / 2) ** 2
        line = 2 * 6371.0 * np.arcsin(np.sqrt(h))
        others = [m for m in range(k) if m not in (i, j)]
        direct = not np.any(R[i, others] + R[others, j] <= R[i, j] + 0.125)
        out.append(dict(counter_a=C.counter_id[i], counter_b=C.counter_id[j], road_km=round(R[i, j], 1), line_km=round(line, 1),
                        same_route=C.route_ja[i] == C.route_ja[j], direct=direct, _slack=plen[i] / 2 + plen[j] / 2))
O = pd.DataFrame(out)
assert (O.counter_a < O.counter_b).all() and O.road_km.gt(0).all() and not O.duplicated(["counter_a", "counter_b"]).any()
bad = O[O.line_km > O.road_km + O._slack + 0.5]
print(f"pairs within {MAXKM} km by road: {len(O)}, direct {int(O.direct.sum())}, counters with a neighbour "
      f"{len(set(O.counter_a) | set(O.counter_b))}/{k}, impossible {len(bad)}")
print(bad.assign(over=(bad.line_km - bad.road_km - bad._slack).round(2)).to_string())
assert len(bad) <= 0.01 * len(O), "too many pairs with a straight line longer than the road"
O.drop(columns="_slack").to_csv("census/road_graph_2021.csv", index=False)
C.to_csv("census/census_r3_placed.csv", index=False)
