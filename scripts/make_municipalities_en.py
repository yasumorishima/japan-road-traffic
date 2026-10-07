"""English names of Japanese municipalities, written to data/municipalities_en.csv for build_kaggle.py.

  python scripts/make_municipalities_en.py <MIC code list .xlsx>   (needs openpyxl and internet; run by hand when the
                                                                   list changes; the output is committed)

Sources:
- the MIC local government code list (総務省「全国地方公共団体コード」, https://www.soumu.go.jp/denshijiti/code.html):
  codes, Japanese names and official readings in kana;
- Wikidata (CC0): the English label of the item that carries the code (property P429).
The name comes from Wikidata, macrons removed (Chūō -> Chuo). Readings cannot be romanised from the kana alone: the
list writes small kana full size (ナカノジヨウ for Nakanojō) and does not mark word boundaries (トヨウラ is Toyoura,
not Toyora). The type of municipality comes from the end of the official reading and is kept as a suffix: 市 -shi,
区 -ku, 町 -cho or -machi, 村 -mura or -son. A ward of a designated city reads "Sapporo-shi Chuo-ku". The script stops
when a code has no English label or two different ones."""
import json, os, re, sys, unicodedata, urllib.parse, urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "data", "municipalities_en.csv")
SPARQL = "SELECT ?code ?en WHERE { ?item wdt:P429 ?code . ?item rdfs:label ?en FILTER(lang(?en) = 'en') }"
SUFFIX = {"市": {"シ": "shi"}, "区": {"ク": "ku"}, "町": {"チョウ": "cho", "マチ": "machi", "チヨウ": "cho"},
          "村": {"ムラ": "mura", "ソン": "son"}}
TYPE_WORD = re.compile(r"[- ](shi|ku|cho|machi|mura|son|city|town|village|ward)$", re.I)

def kata(s):
    """Half-width katakana (as in the list) to full width, voicing marks combined."""
    return unicodedata.normalize("NFKC", s)

def plain(label):
    """'Chūō-ku, Sapporo' -> 'Chuo'; "Sanʼyō-Onoda" -> "San'yo-Onoda"."""
    s = unicodedata.normalize("NFKD", label.replace("ʼ", "'").replace("’", "'"))
    s = "".join(c for c in s if not unicodedata.combining(c)).split(",")[0].strip()
    s = TYPE_WORD.sub("", s)
    if not re.fullmatch(r"[A-Za-z][A-Za-z' -]*", s):
        raise ValueError(f"unexpected English label {label!r}")
    return s

def suffix(name, reading):
    for k, v in SUFFIX.get(name[-1], {}).items():
        if reading.endswith(k):
            return v
    raise ValueError(f"{name}: reading {reading!r} does not end as a {name[-1]} should")

def wikidata():
    url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": SPARQL, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": "japan-road-traffic/1.0 (https://github.com/yasumorishima/japan-road-traffic)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        rows = json.load(r)["results"]["bindings"]
    labels = {}
    for b in rows:
        code, en = b["code"]["value"], b["en"]["value"].strip()
        if re.fullmatch(r"\d{6}", code) and en:
            labels.setdefault(code[:5], set()).add(en)
    return labels

def main():
    import pandas as pd
    sheets = pd.read_excel(sys.argv[1], sheet_name=None, dtype=str)
    parts = []
    for df in list(sheets.values())[:2]:  # current bodies; wards of designated cities
        df = df.iloc[:, :5]
        df.columns = ["code", "prefecture", "municipality", "prefecture_kana", "municipality_kana"]
        parts.append(df.dropna(subset=["municipality"]))
    m = pd.concat(parts).drop_duplicates("code").reset_index(drop=True)
    if not m.code.str.fullmatch(r"\d{6}").all():
        sys.exit("unexpected code format in the list")
    m["municipality_code"] = m.code.str[:5]
    m["municipality_kana"] = m.municipality_kana.map(kata)
    labels = wikidata()
    def base(code):
        got = {plain(x) for x in labels.get(code, ())}
        if len(got) != 1:
            sys.exit(f"{code}: English label from Wikidata is {sorted(got) or 'missing'}")
        return got.pop()
    names = dict(zip(m.municipality, zip(m.municipality_code, m.municipality_kana)))
    cities = [n for n in names if n.endswith("市") and any(o != n and o.startswith(n) and o.endswith("区") for o in names)]
    out = []
    for r in m.itertuples():
        city = next((c for c in cities if r.municipality != c and r.municipality.startswith(c) and r.municipality.endswith("区")), None)
        en = f"{base(r.municipality_code)}-{suffix(r.municipality, r.municipality_kana)}"
        if city:
            ccode, ckana = names[city]
            if not r.municipality_kana.startswith(ckana):
                sys.exit(f"{r.municipality}: reading does not start with {city}'s")
            en = f"{base(ccode)}-{suffix(city, ckana)} {en}"
        out.append(en)
    m["municipality_en"] = out
    m = m.sort_values("municipality_code")[["municipality_code", "prefecture", "municipality", "municipality_kana", "municipality_en"]]
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        m.to_csv(f, index=False, lineterminator="\n")
    print(f"{len(m)} municipalities -> {OUT}")

if __name__ == "__main__":
    main()
