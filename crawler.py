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

# aDISWeb keeps all state in a server-side session, and VÖBB only has room for so many at once.
# Every page we open by URL starts a new one, and an unused session lingers for ~9 minutes.
# Left open, they fill the pool and VÖBB answers "Ihre Sitzung wurde beendet" to everyone,
# so we end each session ("Sitzung beenden") as soon as we have read what we came for.
def is_record(h): return "Exemplarangaben" in h or "Besitzende Bibliotheken" in h
def refused(h): return "Sitzung wurde beendet" in h or not re.search(r'<form action="', h)

def open_page(url, ok=lambda h: not refused(h)):
    """GET url in a fresh session, waiting while VÖBB has no free sessions. Returns (session, html)."""
    for wait in (0, 5, 10, 20, 40, 60, 120, 180):
        time.sleep(wait)
        S = requests.Session(); S.headers["User-Agent"] = UA
        try:
            h = S.get(url, timeout=45).text
            if ok(h): return S, h
        except requests.RequestException: pass
    return None, ""

def end_session(S, h):
    if S and h and not refused(h):
        try: post(S, h, {"scriptEnabled": "true", "$Tab": "0", "selected": "*SE"})
        except requests.RequestException: pass

def hits(h):
    out = []
    for li in re.findall(r'<li class="rList_li.*?</li>', h, re.S):
        m = re.search(r'sp=(SAK\d+)" id="[^"]+">([^<]*)</a>', li)
        med = re.search(r'rList_medium[^>]*><img[^>]*alt="([^"]*)"', li)
        if m: out.append((m.group(1), html.unescape(m.group(2)).strip(), med.group(1) if med else ""))
    return out

def search(term, sort):
    # VÖBB sometimes drops a connection mid-search. Paging depends on the session's form state,
    # so start that search over rather than retrying the one page.
    for attempt in range(3):
        try: return search_once(term, sort)
        except requests.RequestException as e:
            log(f"'{term}' ({sort or 'default'}): {type(e).__name__}, starting this search again")
            time.sleep(30)
    sys.exit(f"'{term}' failed three times, keeping the old games.json")

def search_once(term, sort):
    S, h = open_page(START)
    if not S: sys.exit("VÖBB refused new sessions for over 7 minutes")
    h = post(S, h, {"$Autosuggest": term, "$Select": "Bibliotheksbestand", "$Button": "Suchen"})
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
    end_session(S, h)
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

def copies(sak):
    S, h = open_page(f"{START}&sp={sak}", ok=is_record)
    end_session(S, h)
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
    n, lock = [0, 0], threading.Lock()
    def job(r):
        r["copies"] = copies(r["id"]); time.sleep(0.3)
        with lock:
            n[0] += 1; n[1] += not r["copies"]
            if n[0] % 200 == 0: log(f"  {n[0]}/{len(recs)}, {n[1]} without copies")
    with ThreadPoolExecutor(WORKERS) as ex: list(ex.map(job, recs.values()))
    log(f"{n[1]} records came back without copies")
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
