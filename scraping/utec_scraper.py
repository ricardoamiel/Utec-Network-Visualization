"""
utec_scraper.py  (v8 – Picture & Meta Enrichment Fixed)
==========================================
Fix 1: verify_cf() warning suppressed — install opencv-python to enable
        automatic Turnstile checkbox clicking (optional but nice to have).
Fix 2: profile pages use selector="h1" instead of "a.link.person"
        (a.link.person only exists on list pages, not profile pages).
Fix 3: Agregado soporte avanzado para etiquetas <picture> e <img> de alta resolución (?w=160)
Fix 4: Extracción recursiva de correo y roles directo desde el perfil individual si falló en la lista.

Setup:
    pip install nodriver beautifulsoup4 lxml
    pip install opencv-python          # optional: enables auto-CAPTCHA click

Run:
    python scraping/utec_scraper.py
    CHROME_PATH=/usr/bin/chromium python scraping/utec_scraper.py  # override browser

Output: data/utec_raw_professors.json
"""

import asyncio, json, logging, os, re, random, shutil
from bs4 import BeautifulSoup
import nodriver as uc

# ──────────────────────────────────────────────────────────────────────────────
BASE_URL  = "https://cris.utec.edu.pe"
DIR_URL   = "https://cris.utec.edu.pe/es/persons/"
OUT_PATH  = os.path.join("data", "utec_raw_professors.json")
PAGE_SIZE = 50
DELAY_MIN = 2.0
DELAY_MAX = 4.5

LIST_SEL    = "a.link.person"   # present on directory/list pages
PROFILE_SEL = "h1"              # present on every individual profile page

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
#  FIND CHROME BINARY
# ──────────────────────────────────────────────────────────────────────────────
def find_chrome() -> str:
    candidates = [
        "/opt/google/chrome/chrome",
        "/opt/google/chrome-beta/chrome",
        "/snap/bin/google-chrome",
        "/snap/bin/chromium",
        "google-chrome-stable", "google-chrome",
        "chromium-browser", "chromium",
        "microsoft-edge", "brave-browser",
    ]
    for c in candidates:
        path = c if os.path.isabs(c) else shutil.which(c)
        if path and os.path.isfile(path):
            log.info("Browser binary: %s", path)
            return path
    raise FileNotFoundError(
        "No Chrome/Chromium found. "
        "Set CHROME_PATH=/path/to/chrome or install Chrome."
    )


# ──────────────────────────────────────────────────────────────────────────────
#  NAVIGATION
#  selector param lets caller choose what element to wait for:
#    LIST_SEL    = "a.link.person"  → directory listing pages
#    PROFILE_SEL = "h1"             → individual profile pages
# ──────────────────────────────────────────────────────────────────────────────
async def goto(tab, url, content_timeout=30, selector=LIST_SEL):
    await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
    try:
        await tab.get(url)
        await tab.sleep(6)          # let Cloudflare JS challenge auto-resolve

        # Auto-click Turnstile checkbox (requires opencv-python)
        try:
            await tab.verify_cf()
            await tab.sleep(3)
        except Exception:
            pass                    # no checkbox, or opencv not installed

        await tab.wait_for(selector=selector, timeout=content_timeout)
        return True
    except Exception as exc:
        log.warning("goto failed (%s): %s", url, exc)
        return False


# ──────────────────────────────────────────────────────────────────────────────
#  PARSE  –  DIRECTORY PAGE
# ──────────────────────────────────────────────────────────────────────────────
def parse_directory(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    results = []

    items = soup.select("article.list-result-item, li.list-result-item")
    if not items:
        items = soup.select("div.rendering_person_short, div.rendering.person")
    if not items:
        seen, pseudo = set(), []
        for a in soup.select("a[href*='/es/persons/']"):
            href = a.get("href", "")
            if href in seen:
                continue
            seen.add(href)
            node = a
            for _ in range(5):
                if node.name in ("article", "li", "div", "section"):
                    break
                node = node.parent
            pseudo.append(node)
        items = pseudo

    log.info("  %d person blocks found", len(items))

    for item in items:
        try:
            link = (item.select_one("a.link.person")
                    or item.select_one("h3.title a, h2.title a")
                    or item.select_one("a[href*='/es/persons/']"))
            if not link:
                continue

            name        = link.get_text(strip=True)
            href        = link.get("href", "")
            profile_url = href if href.startswith("http") else BASE_URL + href

            mail  = item.select_one("a[href^='mailto:']")
            email = None
            if mail:
                email = (mail.get_text(strip=True)
                         or mail["href"].replace("mailto:", "").strip())

            dept_links = item.select("a[href*='/es/organisations/']")
            dept = dept_links[0].get_text(strip=True) if dept_links else "Unknown"

            groups = [a.get_text(strip=True) for a in item.select(
                "a[href*='/es/research-groups/'], "
                "a[href*='/es/grupos-investigacion/']")]

            role_el = item.select_one("span.type, span.role, .person-role")
            role    = role_el.get_text(strip=True).strip("- ") if role_el else None

            img   = item.select_one(".portrait img, img.avatar, img")
            photo = None
            if img and img.get("src"):
                src   = img["src"]
                photo = src if src.startswith("http") else BASE_URL + src

            results.append(dict(name=name, email=email, dept=dept,
                                groups=groups, role=role,
                                photo_url=photo, profile_url=profile_url))
            log.info("    + %s  [%s]", name, dept)

        except Exception as exc:
            log.debug("Item error: %s", exc)

    return results


# ──────────────────────────────────────────────────────────────────────────────
#  PARSE  –  PROFILE PAGE
#
#  Captures everything visible on a Pure CRIS person page:
#    • Metrics sidebar: h-index, citations, publication count
#    • External links: ORCID, Google Scholar, Scopus, LinkedIn
#    • Research keywords from "Huella digital" section (if already loaded)
#    • Short professional bio
# ──────────────────────────────────────────────────────────────────────────────
def parse_profile(html: str, base: dict) -> dict:
    soup = BeautifulSoup(html, "lxml")
    info = {
        **base,
        "areas":        [],
        "orcid":        None,
        "scholar_url":  None,
        "scopus_url":   None,
        "linkedin_url": None,
        "h_index":      None,
        "citations":    None,
        "pub_count":    None,
        "bio":          None,
    }

    # ══════════════════════════════════════════════════════════════════════════
    # CAMBIOS AGREGADOS: Extracción de Foto HQ (?w=160), Email y Rol
    # ══════════════════════════════════════════════════════════════════════════
    
    # 1. Foto de alta calidad desde la etiqueta <picture> del perfil individual
    profile_pic = soup.select_one("div.portrait picture img, div.rendering_person picture img, picture img, .picture img")
    if profile_pic and profile_pic.get("src"):
        src = profile_pic["src"]
        # Convertimos la ruta relativa (/files-asset/...) en absoluta para el proxy de Flask
        info["photo_url"] = src if src.startswith("http") else BASE_URL + src
        log.info("    -> Foto HQ encontrada: %s", info["photo_url"].split("?")[0])

    # 2. Respaldo para Email si vino como null desde la lista general
    if not info.get("email"):
        mail_el = soup.select_one("a[href^='mailto:']")
        if mail_el:
            info["email"] = mail_el.get_text(strip=True) or mail_el["href"].replace("mailto:", "").strip()

    # 3. Respaldo para Rol Académico si vino como null
    if not info.get("role"):
        role_el = soup.select_one(".rendering_person span.type, .person-role, div.relations p.position")
        if role_el:
            info["role"] = role_el.get_text(strip=True).strip("- ")

    # ══════════════════════════════════════════════════════════════════════════

    # ── External profile links ────────────────────────────────────────────────
    for a in soup.select("a[href]"):
        h = a["href"]
        if "scholar.google" in h and not info["scholar_url"]:
            info["scholar_url"] = h
        if "scopus.com" in h and not info["scopus_url"]:
            info["scopus_url"] = h
        if "linkedin.com" in h and not info["linkedin_url"]:
            info["linkedin_url"] = h
        if "orcid.org" in h and not info["orcid"]:
            m = re.search(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", h)
            if m:
                info["orcid"] = m.group(0)

    # ── Metrics: h-index and citations ───────────────────────────────────────
    # Pure CRIS renders them as: "27  Citas  |  3  Índice h"
    # Strategy: find numbers adjacent to the label text
    full_text = soup.get_text(" ", strip=True)

    m_cit = re.search(r"(\d+)\s*Citas?", full_text, re.I)
    if m_cit:
        info["citations"] = int(m_cit.group(1))

    m_h = re.search(r"(\d+)\s*[ÍI]ndice\s*h", full_text, re.I)
    if m_h:
        info["h_index"] = int(m_h.group(1))

    # ── Publication count from tab label "Publicaciones (13)" ────────────────
    for a in soup.select("a, li, span"):
        m_pub = re.search(r"[Pp]ublicaciones?\s*\((\d+)\)", a.get_text())
        if m_pub:
            info["pub_count"] = int(m_pub.group(1))
            break

    # ── Research keywords (Huella digital / Fingerprint section) ─────────────
    areas = []
    for sel in [
        "div.fingerprints a",        # fingerprint concept links
        "span.concept-tag",          # tagged concepts
        "ul.keywords li",            # keyword list
        "div.keywords a",
        ".research-areas li",
        "a.concept",                 # alternative Pure rendering
    ]:
        for el in soup.select(sel):
            kw = el.get_text(strip=True)
            if kw and len(kw) < 80:
                areas.append(kw)
    info["areas"] = list(dict.fromkeys(areas))[:25]

    # ── Short bio (first 400 chars of professional profile text) ─────────────
    bio_el = soup.select_one(
        ".profile-text, .person-profile p, "
        "div.textblock p, div.rendering_person p"
    )
    if bio_el:
        info["bio"] = bio_el.get_text(strip=True)[:400]

    return info


# ──────────────────────────────────────────────────────────────────────────────
#  TOTAL PAGES
# ──────────────────────────────────────────────────────────────────────────────
def next_page_url(html: str) -> str | None:
    """
    Follow the "Siguiente ›" link instead of guessing ?page=N URLs.
    Returns the absolute URL of the next page, or None if on the last page.
    Handles Pure CRIS pagination which renders as:  1  2  3  Siguiente ›
    """
    soup = BeautifulSoup(html, "lxml")
    for a in soup.select("a"):
        txt = a.get_text(strip=True).lower()
        # Match "Siguiente", "Next", "›", "»" — any next-page indicator
        if any(tok in txt for tok in ("siguiente", "next", "›", "»")):
            href = a.get("href", "").strip()
            if href and href != "#":
                return href if href.startswith("http") else BASE_URL + href
    return None          # no next-page link → we are on the last page


# ──────────────────────────────────────────────────────────────────────────────
#  SCRAPE "RED" (NETWORK) TAB  –  real collaboration edges
#
#  Each Pure CRIS profile has a "Red" tab that lists co-authors by name with
#  links to their own profiles.  This gives us actual edges, not inferred ones.
#
#  Pure renders the network tab at: /es/persons/<slug>/?publications=...
#  but the easiest approach is to click the tab link and read the loaded HTML.
# ──────────────────────────────────────────────────────────────────────────────
async def scrape_network_tab(tab, profile_url: str) -> list[dict]:
    """
    Navigate to the "Red" (Network/Collaboration) tab of a professor profile
    and return a list of collaborators:
        [{"name": "...", "profile_url": "...", "collab_count": N}, ...]
    Returns [] if the tab is not found or fails to load.
    """
    try:
        # The Red tab href is usually: <profile_url>#network  or ?tab=network
        # In Pure CRIS it typically appends /network/ or uses a JS tab.
        # Strategy: find the <a> whose text is "Red" and get its href.
        html = await tab.get_content()
        soup = BeautifulSoup(html, "lxml")

        tab_link = None
        for a in soup.select("a.tab, nav a, ul.nav a, .tab-nav a"):
            if re.search(r"\bred\b|network|colabora", a.get_text(), re.I):
                tab_link = a.get("href", "")
                break

        if not tab_link:
            return []

        net_url = tab_link if tab_link.startswith("http") else BASE_URL + tab_link
        ok = await goto(tab, net_url, content_timeout=20, selector="h1")
        if not ok:
            return []

        net_html = await tab.get_content()
        net_soup = BeautifulSoup(net_html, "lxml")
        collaborators = []

        # Pure renders co-authors as person links in the network section
        for a in net_soup.select("a[href*='/es/persons/']"):
            name = a.get_text(strip=True)
            href = a.get("href", "")
            col_url = href if href.startswith("http") else BASE_URL + href
            if name and col_url != profile_url:
                collaborators.append({"name": name, "profile_url": col_url})

        # Deduplicate
        seen, unique = set(), []
        for c in collaborators:
            if c["profile_url"] not in seen:
                seen.add(c["profile_url"])
                unique.append(c)

        log.info("    Network tab: %d collaborators", len(unique))
        return unique

    except Exception as exc:
        log.debug("Network tab scrape failed: %s", exc)
        return []


# ──────────────────────────────────────────────────────────────────────────────
#  CRAWL
# ──────────────────────────────────────────────────────────────────────────────
async def crawl() -> list[dict]:
    chrome  = os.environ.get("CHROME_PATH") or find_chrome()
    browser = await uc.start(
        headless=False,
        lang="es-PE",
        browser_executable_path=chrome,
        browser_args=["--start-maximized"],
    )
    tab = browser.main_tab

    log.info("Browser opened. Cloudflare will auto-resolve in ~6 s.")
    log.info("If a checkbox CAPTCHA appears, click it in the browser window.")

    # ── Page 1 ────────────────────────────────────────────────────────────────
    ok = await goto(tab, DIR_URL, content_timeout=120, selector=LIST_SEL)
    if not ok:
        log.error("Persons list page never loaded.")
        browser.stop()
        return []

    # ── Follow "Siguiente ›" until the last page ─────────────────────────────
    # More robust than guessing ?page=N — works regardless of URL format.
    profs    = []
    page_num = 1
    while True:
        html_page = await tab.get_content()
        batch     = parse_directory(html_page)
        profs.extend(batch)
        log.info("  Page %d: %d persons (total so far: %d)",
                 page_num, len(batch), len(profs))

        nxt = next_page_url(html_page)
        if not nxt:
            log.info("  No 'Siguiente' link — reached last page.")
            break

        log.info("  → Next page: %s", nxt)
        ok = await goto(tab, nxt, selector=LIST_SEL)
        if not ok:
            log.warning("  Failed to load next page — stopping pagination.")
            break
        page_num += 1

    log.info("Directory complete: %d professors.", len(profs))

    # ── Individual profiles  (selector = "h1", always present on profile pages)
    enriched = []
    for i, prof in enumerate(profs):
        log.info("[%d/%d] %s", i + 1, len(profs), prof["name"])
        ok   = await goto(tab, prof["profile_url"],
                          content_timeout=20, selector=PROFILE_SEL)
        if ok:
            html      = await tab.get_content()
            full      = parse_profile(html, prof)
            # Scrape "Red" tab for real co-author edges
            full["collaborators"] = await scrape_network_tab(tab, prof["profile_url"])
            # Return to profile page for next iteration
            await goto(tab, prof["profile_url"],
                       content_timeout=15, selector=PROFILE_SEL)
        else:
            full = {**prof, "areas": [], "orcid": None, "scholar_url": None,
                    "collaborators": []}
        enriched.append(full)

    browser.stop()
    return enriched


# ──────────────────────────────────────────────────────────────────────────────
def main():
    data = uc.loop().run_until_complete(crawl())
    if not data:
        log.error("No data collected.")
        return
    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info("✓  %d professors saved to %s", len(data), OUT_PATH)


if __name__ == "__main__":
    # pip install nodriver beautifulsoup4 lxml opencv-python
    main()