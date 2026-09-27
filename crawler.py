"""Crawl VÖBB (Berlin public libraries) for PS4, PS5, Switch and Switch 2 games.
Plain HTTP, no browser. Writes games.json next to index.html.
Usage: python crawler.py [games.json]"""
import html, json, re, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import requests

B = "https://www.voebb.de"
START = B + "/aDISWeb/app/prod00?sp=SPROD00"
UA = "Mozilla/5.0 (personal console game list, low rate)"
# (search term, platform used when the title doesn't name one)
TERMS = [("PS5", "PS5"), ("PlayStation 5", "PS5"), ("PS4", "PS4"), ("PlayStation 4", "PS4"),
         ("Nintendo Switch", "Switch"), ("Switch 2", "Switch 2")]
SORTS = [None, "Jahr aufwärts"]   # VÖBB stops paging at ~1,340 hits, so read both ends
WORKERS, PAGE_DELAY = 2, 0.5

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)
def clean(s): return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s)).replace("\xa0", " ")).strip()

def post(S, h, extra):
    act = re.search(r'<form action="([^"]+)"', h).group(1)
    d = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', h)); d.update(extra)
    return S.post(B + act, data=d, timeout=60).text

def hits(h):
    out = []
    for li in re.findall(r'<li class="rList_li.*?</li>', h, re.S):
        m = re.search(r'sp=(SAK\d+)" id="[^"]+">([^<]*)</a>', li)
        med = re.search(r'rList_medium[^>]*><img[^>]*alt="([^"]*)"', li)
        if m: out.append((m.group(1), html.unescape(m.group(2)).strip(), med.group(1) if med else ""))
    return out

def search(term, sort):
    S = requests.Session(); S.headers["User-Agent"] = UA
    h = post(S, S.get(START, timeout=30).text, {"$Autosuggest": term, "$Select": "Bibliotheksbestand", "$Button": "Suchen"})
    if sort:
        m = re.search(r'value="%s" id="[^"]+" name="([^"]+)"' % sort, h)
        if m: h = post(S, h, {m.group(1): sort})
    found = []
    for _ in range(80):
        page = hits(h)
        if not page: break
        found += page
        time.sleep(PAGE_DELAY)
        nxt = post(S, h, {"$Toolbar_3.x": "5", "$Toolbar_3.y": "5"})
        if not hits(nxt) or hits(nxt)[0][0] == page[0][0]: break
        h = nxt
    log(f"'{term}' ({sort or 'default'}): {len(found)} hits")
    return found

def platform(title, fallback):
    t = title.lower().replace("-", " ")
    if re.search(r"switch\s*2", t): return "Switch 2"
    if "switch" in t: return "Switch"
    if re.search(r"ps\s*5|playstation\s*5", t): return "PS5"
    if re.search(r"ps\s*4|playstation\s*4", t): return "PS4"
    if re.search(r"xbox|wii|\bpc\b|ps\s*3|playstation\s*3|nintendo\s*(3ds|ds)\b", t): return None
    return fallback

HARDWARE = re.compile(r"^(\[?nintendo switch( 2| lite| oled)?\b(?!.*(spiel|game|sports|party))|playstation\s*[45]\b(?!.*(spiel|game))"
                      r"|konsolenspiele für)|controller|headset|ladestation|\binlay\b|joy-?con|lenkr|racing wheel|beingurt|\bamiibo\b|tasche|case\b", re.I)

local = threading.local()
def copies(sak):
    if not hasattr(local, "S"):
        local.S = requests.Session(); local.S.headers["User-Agent"] = UA
    h = ""
    for _ in range(4):
        try:
            h = local.S.get(f"{START}&sp={sak}", timeout=45).text
            if "Exemplarangaben" in h or "Besitzende Bibliotheken" in h: break
        except requests.RequestException: pass
        time.sleep(5)
    i = h.find("Exemplarangaben"); tb = h[i:h.find("</table>", i)] if i > 0 else ""
    out = []
    for tr in re.findall(r'<tr class="[^"]*rTable_tr[^"]*"[^>]*>(.*?)</tr>', tb, re.S):
        td = [clean(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(td) >= 5:
            out.append({"library": td[0], "shelf": " · ".join(filter(None, td[1:3])), "status": td[4]})
    return out

def main(out):
    t0 = time.time(); recs = {}
    for term, fb in TERMS:
        for sort in SORTS:
            for sak, title, med in search(term, sort):
                if med != "Konsolenspiel" or sak in recs or HARDWARE.search(title): continue
                p = platform(title, fb)
                if p: recs[sak] = {"id": sak, "raw": title, "platform": p}
    log(f"{len(recs)} game records, fetching copies…")
    n = [0]
    def job(r):
        r["copies"] = copies(r["id"]); time.sleep(0.3); n[0] += 1
        if n[0] % 200 == 0: log(f"  {n[0]}/{len(recs)}")
    with ThreadPoolExecutor(WORKERS) as ex: list(ex.map(job, recs.values()))
    merged = {}
    for r in recs.values():
        title = re.sub(r"\s*[;:.,]?\s*\[[^\]]*\]", "", r["raw"]).strip(" ;:.,-") or r["raw"]
        key = (title.lower(), r["platform"])
        if key in merged: merged[key]["copies"] += r["copies"]
        else: merged[key] = {"title": title, "platform": r["platform"], "url": f"{START}&sp={r['id']}", "copies": r["copies"]}
    games = sorted(merged.values(), key=lambda g: g["title"].lower())
    if len(games) < 500: sys.exit(f"Only {len(games)} games found, keeping the old games.json")
    json.dump({"updated": datetime.now(timezone.utc).isoformat(), "games": games},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    log(f"done: {len(games)} games in {time.time()-t0:.0f}s")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "games.json")
