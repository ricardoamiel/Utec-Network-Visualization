"""
scholar_scraper.py
==================
Google Scholar scraper for UTEC professor profiles.
Retrieves h-index, i10-index, citation count, and publication list.

⚠️  IMPORTANT WARNINGS:
    1. Google Scholar has no public API and aggressively blocks scrapers.
    2. Excessive requests will result in IP bans and CAPTCHA challenges.
    3. This code is provided FOR EDUCATIONAL PURPOSES ONLY.
    4. Use scholarly (https://pypi.org/project/scholarly/) for production;
       it handles proxy rotation and CAPTCHA solving.
    5. Always check Google's Terms of Service before scraping.

⚠️  DO NOT RUN THIS AUTOMATICALLY — it is reference code only.

Dependencies:
    pip install requests beautifulsoup4 scholarly fake-useragent

Usage (manual, one-shot):
    python scraping/scholar_scraper.py

Output:
    data/scholar_profiles.json
"""

import time
import random
import json
import os
import logging
import re
from typing import Optional
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURATION  –  tweak these to reduce detection risk
# ──────────────────────────────────────────────────────────────────────────────

SCHOLAR_BASE = "https://scholar.google.com"
SEARCH_URL   = SCHOLAR_BASE + "/scholar?q={query}&hl=es"
PROFILE_URL  = SCHOLAR_BASE + "/citations?user={user_id}&hl=es&sortby=pubdate"

# Generous delays are critical: Scholar starts blocking at ~2–3 req/min
DELAY_MIN  = 8.0   # seconds
DELAY_MAX  = 20.0  # seconds

# Rotate these to reduce fingerprinting
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

OUT_PATH = os.path.join("data", "scholar_profiles.json")

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# ANTI-DETECTION UTILITIES
# ──────────────────────────────────────────────────────────────────────────────

def random_headers() -> dict:
    """Generate realistic browser headers with a rotating User-Agent."""
    ua = random.choice(USER_AGENTS)
    return {
        "User-Agent":      ua,
        "Accept-Language": "es-PE,es;q=0.9,en-US;q=0.7,en;q=0.5",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
        "DNT":             "1",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":  "document",
        "Sec-Fetch-Mode":  "navigate",
        "Sec-Fetch-Site":  "none",
        "Sec-Fetch-User":  "?1",
        "Cache-Control":   "max-age=0",
    }


def make_session(proxy: Optional[dict] = None) -> requests.Session:
    """
    Build a session.  Pass proxy dict if using a proxy pool:
        proxy = {"http": "http://user:pass@host:port",
                 "https": "http://user:pass@host:port"}

    Recommended proxy services for Scholar: Luminati, Smartproxy, Oxylabs.
    """
    s = requests.Session()
    if proxy:
        s.proxies.update(proxy)
    return s


def polite_get(session: requests.Session, url: str,
               retries: int = 3) -> Optional[requests.Response]:
    """GET with random delay, rotating headers, retry logic."""
    wait = random.uniform(DELAY_MIN, DELAY_MAX)
    log.info("  Sleeping %.1fs before request …", wait)
    time.sleep(wait)

    for attempt in range(retries):
        session.headers.update(random_headers())
        try:
            r = session.get(url, timeout=20, allow_redirects=True)

            # Detect CAPTCHA / block
            if r.status_code == 429 or "captcha" in r.text.lower():
                backoff = 60 * (attempt + 1) + random.uniform(0, 30)
                log.warning("  CAPTCHA/rate-limit detected. Backing off %.0fs …", backoff)
                time.sleep(backoff)
                continue

            if r.status_code == 200:
                return r
            else:
                log.warning("  HTTP %d for %s", r.status_code, url)
        except requests.RequestException as e:
            log.error("  Request error (attempt %d/%d): %s", attempt+1, retries, e)
            time.sleep(random.uniform(5, 15))

    return None


# ──────────────────────────────────────────────────────────────────────────────
# SEARCH FOR PROFESSOR
# ──────────────────────────────────────────────────────────────────────────────

def search_professor(session: requests.Session,
                     name: str, institution: str = "UTEC Peru") -> Optional[str]:
    """
    Search Google Scholar for a professor and return their user_id if found.
    The user_id is the `user` parameter in their Scholar profile URL.
    """
    query = quote_plus(f"{name} {institution}")
    url   = SCHOLAR_BASE + f"/scholar?q={query}&hl=es"
    log.info("Searching Scholar for: %s", name)

    r = polite_get(session, url)
    if not r:
        return None

    soup = BeautifulSoup(r.text, "html.parser")

    # Author cards appear in search results as gs_ai_chp links
    for link in soup.select("h4.gs_rt2 a, a.gs_ai_chp"):
        href = link.get("href", "")
        m = re.search(r"user=([A-Za-z0-9_-]+)", href)
        if m:
            log.info("  Found Scholar ID: %s", m.group(1))
            return m.group(1)

    # Fallback: scan all links for citations profile pattern
    for link in soup.find_all("a", href=re.compile(r"/citations\?.*user=")):
        m = re.search(r"user=([A-Za-z0-9_-]+)", link["href"])
        if m:
            return m.group(1)

    log.warning("  Scholar ID not found for %s", name)
    return None


# ──────────────────────────────────────────────────────────────────────────────
# PARSE PROFILE
# ──────────────────────────────────────────────────────────────────────────────

def parse_profile(html: str, user_id: str) -> dict:
    """
    Parse a Google Scholar profile page.
    Returns: h-index, i10-index, citations_all, citations_since5y,
             top publications (title, year, venue, citations).
    """
    soup = BeautifulSoup(html, "html.parser")
    profile: dict = {"user_id": user_id, "profile_url": SCHOLAR_BASE + f"/citations?user={user_id}"}

    # Metrics table  (#gsc_rsb_st)
    table = soup.select_one("table#gsc_rsb_st")
    if table:
        rows = table.select("tr")
        # Row 1: Citations (all / since 2019)
        # Row 2: h-index
        # Row 3: i10-index
        try:
            cells = [td.get_text(strip=True) for td in rows[1].find_all("td")]
            profile["h_index"]  = int(cells[1])
            profile["h_index5y"]= int(cells[2])
        except (IndexError, ValueError):
            profile["h_index"] = None
        try:
            cells = [td.get_text(strip=True) for td in rows[2].find_all("td")]
            profile["i10_index"]  = int(cells[1])
            profile["i10_index5y"]= int(cells[2])
        except (IndexError, ValueError):
            profile["i10_index"] = None
        try:
            cells = [td.get_text(strip=True) for td in rows[0].find_all("td")]
            profile["citations_all"]  = int(cells[1].replace(",", ""))
            profile["citations_5y"]   = int(cells[2].replace(",", ""))
        except (IndexError, ValueError):
            profile["citations_all"] = None

    # Affiliation / interests
    aff = soup.select_one("#gsc_prf_in")
    profile["affiliation"] = aff.get_text(strip=True) if aff else None

    interests = [a.get_text(strip=True) for a in soup.select("#gsc_prf_int a")]
    profile["interests"] = interests

    # Top publications (first 20)
    pubs = []
    for row in soup.select("tr.gsc_a_tr")[:20]:
        title_el  = row.select_one(".gsc_a_t a")
        year_el   = row.select_one(".gsc_a_y span")
        venue_el  = row.select_one(".gsc_a_t .gsc_a_ex")
        cites_el  = row.select_one(".gsc_a_c a.gsc_a_ac")
        if title_el:
            pubs.append({
                "title":    title_el.get_text(strip=True),
                "year":     year_el.get_text(strip=True) if year_el else None,
                "venue":    venue_el.get_text(strip=True) if venue_el else None,
                "citations": int(cites_el.get_text(strip=True).replace(",",""))
                             if cites_el and cites_el.get_text(strip=True).isdigit() else 0,
                "pub_url":  SCHOLAR_BASE + title_el["href"] if title_el.get("href") else None,
            })
    profile["top_publications"] = pubs
    return profile


# ──────────────────────────────────────────────────────────────────────────────
# BATCH SCRAPE
# ──────────────────────────────────────────────────────────────────────────────

def scrape_scholars(professors: list[dict],
                    proxy: Optional[dict] = None) -> list[dict]:
    """
    Main batch function.  Takes a list of professor dicts (with 'name' key)
    and returns enriched dicts with Scholar metrics.
    """
    session = make_session(proxy)
    results = []

    for i, prof in enumerate(professors):
        log.info("[%d/%d] Processing: %s", i + 1, len(professors), prof["name"])
        result = {**prof}

        # If we already have a scholar_url from UTEC profile, extract user_id
        user_id = None
        if prof.get("scholar_url"):
            m = re.search(r"user=([A-Za-z0-9_-]+)", prof["scholar_url"])
            if m:
                user_id = m.group(1)

        # Otherwise, search for the professor
        if not user_id:
            user_id = search_professor(session, prof["name"])

        if user_id:
            url = PROFILE_URL.format(user_id=user_id)
            r   = polite_get(session, url)
            if r:
                scholar_data = parse_profile(r.text, user_id)
                result.update(scholar_data)
                log.info("  ✓ h-index=%s  citations=%s",
                         scholar_data.get("h_index"), scholar_data.get("citations_all"))
        else:
            log.warning("  ✗ No Scholar profile found.")

        results.append(result)

        # Extra courtesy pause every 5 professors
        if (i + 1) % 5 == 0:
            extra = random.uniform(30, 60)
            log.info("Courtesy break: sleeping %.0fs …", extra)
            time.sleep(extra)

    return results


# ──────────────────────────────────────────────────────────────────────────────
# ALTERNATIVE: scholarly library (recommended for production)
# ──────────────────────────────────────────────────────────────────────────────

def scrape_with_scholarly(professors: list[dict]) -> list[dict]:
    """
    Production-grade alternative using the `scholarly` library which handles
    proxy rotation and free-proxy integration automatically.

    Install:  pip install scholarly
    Docs:     https://scholarly.readthedocs.io
    """
    try:
        from scholarly import scholarly, ProxyGenerator
    except ImportError:
        raise ImportError("Install 'scholarly': pip install scholarly")

    # Optional: set up free proxies (Tor, or a paid proxy service)
    # pg = ProxyGenerator()
    # pg.FreeProxies()                      # ← Free proxies (unstable)
    # pg.Luminati(usr=..., passwd=..., ...)  # ← Paid proxy (stable)
    # scholarly.use_proxy(pg)

    results = []
    for prof in professors:
        log.info("scholarly: searching for %s …", prof["name"])
        try:
            search_q = scholarly.search_author(prof["name"] + " UTEC")
            author   = next(search_q, None)
            if author:
                author   = scholarly.fill(author)  # fetch full profile
                results.append({
                    **prof,
                    "h_index":       author.get("hindex"),
                    "citations_all": author.get("citedby"),
                    "i10_index":     author.get("i10index"),
                    "interests":     author.get("interests", []),
                    "top_publications": [
                        {"title": p["bib"].get("title"),
                         "year":  p["bib"].get("pub_year"),
                         "citations": p.get("num_citations", 0)}
                        for p in author.get("publications", [])[:20]
                    ],
                })
                time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
            else:
                log.warning("  Not found: %s", prof["name"])
                results.append(prof)
        except StopIteration:
            results.append(prof)

    return results


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main():
    # Load professors from UTEC scraper output (or use sample list)
    utec_path = os.path.join("data", "utec_raw_professors.json")
    if os.path.exists(utec_path):
        with open(utec_path, encoding="utf-8") as f:
            professors = json.load(f)
    else:
        # Minimal sample list for testing
        professors = [
            {"name": "Carlos Villanueva", "dept": "Computer Science"},
            {"name": "Ana Ramírez",       "dept": "Computer Science"},
        ]

    # ── CHOOSE ONE scraping strategy ──────────────────────────────────────────
    # Option A: raw requests (educational; brittle against Scholar)
    # results = scrape_scholars(professors)

    # Option B: scholarly library (recommended; handles detection better)
    results = scrape_with_scholarly(professors)
    # ──────────────────────────────────────────────────────────────────────────

    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    log.info("✓ Saved %d profiles to %s", len(results), OUT_PATH)


if __name__ == "__main__":
    # ⚠️  Run manually only — not as part of any automated pipeline.
    main()