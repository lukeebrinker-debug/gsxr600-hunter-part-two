#!/usr/bin/env python3
"""
GSX-R600 (1993) hunter - big edition. Sweeps ~100 sources and notifies via ntfy/Telegram.

  python bike_hunter_part_two.py            # check; alert only on NEW matches
  python bike_hunter_part_two.py --daily    # check; ALWAYS send a status report
  python bike_hunter_part_two.py --test     # diagnostic: prints per-source results/errors, sends nothing

Env vars: NTFY_TOPIC (required), TELEGRAM_TOKEN / TELEGRAM_CHAT_ID (optional)
"""
import json, os, re, sys, time
from pathlib import Path
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "change-me")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
SEEN_FILE = Path(__file__).with_name("seen.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept-Language": "en-US,en;q=0.9"}

# ================= MATCHING (GSX-R600 only) =================
M600_RE = re.compile(r"gsx[\s-]?r[\s-]*600", re.I)
YEAR_RE = re.compile(r"(?<!\d)(1993|'93|93)(?!\d)")
YEAR_LOOSE_RE = re.compile(r"(?<!\d)(1992|1994|'92|'94)(?!\d)")
COLOR_HINT = re.compile(r"red|white|orange", re.I)


def score(title):
    if not M600_RE.search(title):
        return False, ""
    year_ok = YEAR_RE.search(title)
    if not (year_ok or YEAR_LOOSE_RE.search(title)):
        return False, ""
    if year_ok:
        return True, "STRONG (600 '93 red/white)" if COLOR_HINT.search(title) else "MATCH (600 '93)"
    return True, "possible (600, nearby year)"


# ================= FETCH HELPERS =================
def get(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


_pw = _browser = None


def get_js(url):
    """JavaScript-heavy pages. One shared browser for the whole run."""
    global _pw, _browser
    from playwright.sync_api import sync_playwright
    if _browser is None:
        _pw = sync_playwright().start()
        _browser = _pw.chromium.launch()
    page = _browser.new_page(user_agent=HEADERS["User-Agent"])
    try:
        page.goto(url, wait_until="networkidle", timeout=45000)
        return page.content()
    finally:
        page.close()


def parse_feed(xml):
    """Handles both RSS (<item>) and Atom (<entry>). Returns [(title+text, link)]."""
    soup = BeautifulSoup(xml, "xml")
    out = []
    for it in soup.find_all(["item", "entry"]):
        title = it.title.get_text(" ", strip=True) if it.title else ""
        body = ""
        for tag in ("description", "content", "summary"):
            t = it.find(tag)
            if t:
                body = BeautifulSoup(t.get_text(), "html.parser").get_text(" ", strip=True)[:200]
                break
        link = ""
        l = it.find("link")
        if l:
            link = l.get("href") or l.get_text(strip=True)
        out.append((f"{title} {body}".strip(), link or title))
    return out


def generic_page(name, url, js=False):
    soup = BeautifulSoup(get_js(url) if js else get(url), "html.parser")
    seen_urls, out = set(), []
    for a in soup.find_all("a", href=True):
        full = urljoin(url, a["href"])
        if full in seen_urls:
            continue
        text = a.get_text(" ", strip=True) or a.get("title", "")
        ctx = a.parent.get_text(" ", strip=True)[:200] if a.parent else ""
        title = text if ctx in text else f"{text} {ctx}"
        if len(title.strip()) > 5:
            seen_urls.add(full)
            out.append((title[:250], full, name))
    return out


def run_all(name, urls, fn, delay=1.0):
    """Run fn(url) for each url; only fail if EVERY url failed."""
    out, errs = [], []
    for u in urls:
        try:
            out += fn(u)
        except Exception as e:
            errs.append(e)
        time.sleep(delay)
    if errs and len(errs) == len(urls):
        raise errs[-1]
    return out


# ================= SOURCE GROUPS =================
# ---- eBay Motors (RSS) ----
def src_ebay():
    qs = ["suzuki gsxr 600 1993", "suzuki gsx-r600 1993", "gsxr600 1993 red white",
          "1993 gsxr 600", "93 gsxr 600", "gsxr 600 1992 1993 1994 project"]
    urls = []
    for q in qs:
        base = f"https://www.ebay.com/sch/6024/i.html?_nkw={quote_plus(q)}&_rss=1&_sop=10"
        urls += [base, base + "&LH_BIN=1", base + "&LH_Auction=1"]
    def f(u):
        return [(t, l, "eBay Motors") for t, l in parse_feed(get(u))]
    return run_all("eBay", urls, f, 0.5)


# ---- Craigslist (RSS, city by city) ----
CL_CITIES = """newyork losangeles chicago houston phoenix philadelphia sanantonio sandiego dallas sfbay
austin jacksonville seattle denver washingtondc boston nashville detroit portland lasvegas memphis
louisville baltimore milwaukee albuquerque tucson fresno sacramento kansascity atlanta miami raleigh
omaha minneapolis cleveland tulsa oklahomacity neworleans tampa orangecounty inlandempire columbus
indianapolis cincinnati pittsburgh stlouis saltlakecity charlotte orlando richmond hartford providence
buffalo rochester madison desmoines boise spokane birmingham charleston greenville knoxville chattanooga
lexington grandrapids toledo dayton albany norfolk elpaso lubbock reno lincoln wichita littlerock
honolulu anchorage fortcollins colorado springfield""".split()


def src_craigslist():
    urls = [f"https://{c}.craigslist.org/search/mca?query=gsxr&min_auto_year=1992&max_auto_year=1994&format=rss"
            for c in CL_CITIES]
    def f(u):
        city = u.split("//")[1].split(".")[0]
        return [(t, l, f"Craigslist {city}") for t, l in parse_feed(get(u))]
    return run_all("Craigslist", urls, f, 0.4)


# ---- Reddit (RSS: site-wide + sport-bike subs) ----
def src_reddit():
    urls = ["https://www.reddit.com/search.rss?sort=new&q=" + quote_plus(q) for q in
            ["gsxr600 1993", "gsx-r600 1993 for sale", "1993 gsxr 600"]]
    for sub in ["Sportbikes", "motorcycles", "MotorcycleMarket", "motorcyclesforsale", "Suzuki", "GSXR"]:
        urls.append(f"https://www.reddit.com/r/{sub}/search.rss?restrict_sr=on&sort=new&q=gsxr600")
    def f(u):
        return [(t, l, "Reddit") for t, l in parse_feed(get(u))]
    return run_all("Reddit", urls, f, 1.5)


# ---- Web-wide sweep via Bing RSS: catches forums, Facebook/Instagram public posts, dealers ----
BING_BASE = ['"gsxr 600" 1993 for sale', '"gsx-r600" 1993 for sale', "1993 suzuki gsxr600 red white",
             "1993 gsxr 600 orange red wheels", "1993 Suzuki GSX-R600 auction", '"93 gsxr 600" for sale']
BING_SITES = ["gsxr.com", "sportbikes.net", "reddit.com", "facebook.com", "instagram.com", "advrider.com",
              "cycletrader.com", "craigslist.org", "offerup.com", "ebay.com", "bringatrailer.com",
              "hemmings.com", "motohunt.com", "hibid.com", "copart.com", "carsandbids.com", "mecum.com",
              "bonhams.com", "classiccars.com", "racingjunk.com", "hagerty.com", "liveauctioneers.com",
              "proxibid.com", "iaai.com", "bid.cars", "rumbleon.com", "mercari.com", "nextdoor.com",
              "pinterest.com", "youtube.com", "vintagejapaneseclassics.com"]


def src_bing():
    qs = BING_BASE + [f'site:{s} "gsxr 600" 1993' for s in BING_SITES]
    urls = ["https://www.bing.com/search?format=rss&count=30&q=" + quote_plus(q) for q in qs]
    def f(u):
        return [(t, l, "Web search") for t, l in parse_feed(get(u))]
    return run_all("Bing", urls, f, 1.5)


# ---- Individual sites (plain HTML or JS-rendered). URL patterns are best guesses; the
# ---- daily report tells you which ones return nothing so they can be fixed.
SITES = {
    # classifieds / aggregators
    "Cycle Trader": (["https://www.cycletrader.com/motorcycles-for-sale/suzuki/gsx-r600?year=1992-1994",
                      "https://www.cycletrader.com/motorcycles-for-sale?keyword=gsxr600"], False),
    "MotoHunt": (["https://www.motohunt.com/motorcycles-for-sale/suzuki/gsx-r600"], False),
    "RumbleOn": (["https://www.rumbleon.com/inventory?make=suzuki&model=gsx-r600"], True),
    "OfferUp": (["https://offerup.com/search?q=gsxr600"], True),
    "Mercari": (["https://www.mercari.com/search/?keyword=gsxr600"], True),
    "ClassifiedAds": (["https://www.classifiedads.com/search.php?keywords=gsxr600"], False),
    "Recycler": (["https://www.recycler.com/search?q=gsxr600"], False),
    "RacingJunk": (["https://www.racingjunk.com/search/?q=gsxr600"], False),
    "ClassicCars.com": (["https://classiccars.com/listings/find?q=gsxr"], False),
    # enthusiast forums (search pages; some require login and will report as empty/failed)
    "GSXR.com forum": (["https://www.gsxr.com/forum/search/?q=gsxr600+1993&o=date"], False),
    "Sportbikes.net forum": (["https://www.sportbikes.net/forum/search/?q=gsxr600+1993&o=date"], False),
    # collector / enthusiast auctions
    "Iconic Motorbike Auctions": (["https://iconicauctioneers.com/?s=gsxr"], False),
    "Hemmings": (["https://www.hemmings.com/classifieds/cars-for-sale?q=gsx-r600"], False),
    "Mecum": (["https://www.mecum.com/lots/?s=gsxr"], False),
    "Bonhams": (["https://www.bonhams.com/search/?q=gsxr"], True),
    "Cars & Bids": (["https://carsandbids.com/search?q=gsxr"], True),
    "Collecting Cars": (["https://collectingcars.com/search?q=gsxr"], True),
    "Hagerty Marketplace": (["https://www.hagerty.com/marketplace/search?q=gsxr"], True),
    # local & general auctions
    "HiBid": (["https://hibid.com/lots?q=gsxr600"], True),
    "Proxibid": (["https://www.proxibid.com/asp/SearchAdvanced_i.asp?searchTerm=gsxr"], True),
    "LiveAuctioneers": (["https://www.liveauctioneers.com/search/?q=gsxr600"], True),
    "GovDeals": (["https://www.govdeals.com/en/search?q=gsxr"], True),
    "PublicSurplus": (["https://www.publicsurplus.com/sms/browse/search?posting=y&keyWord=gsxr"], False),
    "Municibid": (["https://municibid.com/Browse?q=gsxr"], False),
    # salvage / insurance auctions
    "Copart": (["https://www.copart.com/lotSearchResults?free=true&query=gsxr600"], True),
    "IAA": (["https://www.iaai.com/vehiclesearch/searchkeyword?keyword=gsxr600"], True),
    "Bid.cars": (["https://bid.cars/en/search/results?search-type=filters&type=Motorcycle&make=Suzuki&model=GSX-R600"], True),
    "SalvageBid": (["https://www.salvagebid.com/search?q=gsxr600"], True),
}


def site_source(name):
    urls, js = SITES[name]
    return lambda: run_all(name, urls, lambda u: generic_page(name, u, js), 1.0)


def src_bat():
    def f(u):
        return generic_page("Bring a Trailer", u)
    return run_all("BaT", ["https://bringatrailer.com/?s=gsx-r", "https://bringatrailer.com/?s=gsxr"], f)


SOURCES = {"eBay Motors": src_ebay, "Craigslist": src_craigslist, "Reddit": src_reddit,
           "Web search (forums/FB/dealers)": src_bing, "Bring a Trailer": src_bat}
for _n in SITES:
    SOURCES[_n] = site_source(_n)
# Facebook Marketplace & groups: login required / blocks bots. Covered only indirectly through the
# Bing sweep (public posts). Use Facebook's own saved-search alerts for real coverage.


# ================= NOTIFY =================
def notify(title, body, url=None, priority="default"):
    try:
        h = {"Title": title.encode("utf-8"), "Priority": priority}
        if url and url.startswith("http"):
            h["Click"] = url
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=body.encode("utf-8"), headers=h, timeout=20)
    except Exception as e:
        print("ntfy failed:", e)
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                          data={"chat_id": TELEGRAM_CHAT_ID,
                                "text": f"{title}\n{body}" + (f"\n{url}" if url else "")}, timeout=20)
        except Exception as e:
            print("telegram failed:", e)


# ================= MAIN =================
def main():
    daily = "--daily" in sys.argv
    test = "--test" in sys.argv
    seen = set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()
    new_hits, ok, empty, failed = [], [], [], []

    for name, fn in SOURCES.items():
        try:
            results = fn()
            (ok if results else empty).append(name)
            if test:
                hits = sum(1 for t, u, src in results if score(t)[0])
                print(f"{'OK    ' if results else 'EMPTY '} {name}: {len(results)} listings read, {hits} matches", flush=True)
            for title, url, source in results:
                good, label = score(title)
                if good and (test or url not in seen):
                    seen.add(url)
                    new_hits.append((label, title, url, source))
        except Exception as e:
            failed.append(f"{name} ({type(e).__name__})")
            if test:
                print(f"FAILED {name}: {type(e).__name__}: {str(e)[:200]}", flush=True)

    if _browser:
        _browser.close()
        _pw.stop()
    if test:
        print(f"\nSUMMARY: {len(ok)} working, {len(empty)} empty, {len(failed)} failed, "
              f"{len(new_hits)} matching listings found right now")
        for label, title, url, source in new_hits:
            print(f"  MATCH [{label}] {source}: {title[:100]} -> {url}")
        return
    SEEN_FILE.write_text(json.dumps(sorted(seen)))

    for label, title, url, source in new_hits:
        notify(f"{label} - {source}", title, url,
               priority="high" if label.startswith(("STRONG", "MATCH")) else "default")

    if daily:
        lines = ["Nothing found today." if not new_hits else f"{len(new_hits)} new matches.",
                 f"Working: {len(ok)}/{len(SOURCES)} sources."]
        if empty:
            lines.append("Returned nothing (maybe need fixing): " + ", ".join(empty))
        if failed:
            lines.append("Failed/blocked: " + ", ".join(failed))
        notify("GSX-R600 daily report", "\n".join(lines))


if __name__ == "__main__":
    main()
