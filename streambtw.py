import asyncio
import re
import requests
import logging
from datetime import datetime
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# ==================== CONFIG ====================
BASE_URL = "https://streambtw.com/"
IFRAME_ORIGIN = "https://streambtw.live"
PLAYLIST_FILE = "StreamBTW.m3u8"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"

CUSTOM_HEADERS = {
    "Origin": IFRAME_ORIGIN,
    "Referer": f"{IFRAME_ORIGIN}/",
    "User-Agent": USER_AGENT
}

DEFAULT_LOGO = "http://drewlive24.duckdns.org:9000/Logos/DrewLiveSports.png"

TV_IDS = {
    "premier league": "Soccer.Dummy.us",
    "laliga": "Soccer.Dummy.us",
    "serie a": "Soccer.Dummy.us",
    "ligue 1": "Soccer.Dummy.us",
    "nfl": "Football.Dummy.us",
    "nba": "Basketball.Dummy.us",
    "other": "Sports.Dummy.us"
}

# ==================== LOGGING ====================
logging.basicConfig(
    filename="streambtw.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", "%H:%M:%S"))
logging.getLogger("").addHandler(console)
log = logging.getLogger("StreamBTW")

# ==================== HELPERS ====================

def strip_non_ascii(text: str) -> str:
    return re.sub(r"[^\x00-\x7F]+", "", text or "")

def get_html(url: str) -> str:
    try:
        res = requests.get(url, headers=CUSTOM_HEADERS, timeout=12)
        res.raise_for_status()
        return res.text
    except Exception as e:
        log.warning(f"Failed to GET {url}: {e}")
        return ""

def normalize_iframe_url(href: str) -> str:
    if not href:
        return None
    href = href.strip()
    parsed = urlparse(href)
    if parsed.scheme:
        return href
    return urljoin(IFRAME_ORIGIN, href)

def parse_matches():
    html = get_html(BASE_URL)
    if not html:
        log.info("Trying fallback host: streambtw.live")
        html = get_html(IFRAME_ORIGIN + "/")
        if not html:
            log.warning("Both hosts failed to return HTML.")
            return []

    soup = BeautifulSoup(html, "html.parser")
    matches = []

    for card in soup.select("div.card"):
        league_tag = card.select_one(".card-title")
        match_tag = card.select_one(".card-text")
        img_tag = card.select_one("img")
        a_tag = card.select_one("a.btn, a[href]")

        league = strip_non_ascii(league_tag.text.strip()) if league_tag else "Other"
        match_name = strip_non_ascii(match_tag.text.strip()) if match_tag else "Unknown Match"
        logo = img_tag["src"].strip() if img_tag and img_tag.get("src") else DEFAULT_LOGO
        iframe_href = a_tag["href"].strip() if a_tag and a_tag.get("href") else None
        iframe = normalize_iframe_url(iframe_href) if iframe_href else None

        if iframe:
            matches.append({"league": league, "match": match_name, "logo": logo, "iframe": iframe})

    log.info(f"✅ Found {len(matches)} cards")
    return matches

# ==================== PLAYWRIGHT extractor ====================

async def extract_m3u8(page, embed_url):
    found = None
    try:
        async def on_request(request):
            nonlocal found
            if ".m3u8" in request.url and not found:
                found = request.url
                log.info(f"  ⚡ Detected .m3u8: {found}")

        page.on("request", on_request)
        await page.goto(embed_url, wait_until="domcontentloaded", timeout=10000)
        await asyncio.sleep(1.0)

        selectors = [
            "button", "div[role='button']", ".vjs-big-play-button",
            ".jw-icon-playback", ".plyr__control", "div[class*='play']"
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    try:
                        await el.click(timeout=600)
                        log.info(f"  👆 clicked selector {sel}")
                        await asyncio.sleep(0.5)
                    except Exception:
                        pass
            except Exception:
                pass

        for _ in range(12):
            if found:
                break
            await asyncio.sleep(0.5)

        if not found:
            html = await page.content()
            m = re.findall(r"https?://[^\s\"']+\.m3u8[^\s\"']*", html)
            if m:
                found = m[0]
                log.info(f"  🕵️ fallback m3u8 from HTML: {found}")

        return found
    except Exception as e:
        log.warning(f"Error extracting from {embed_url}: {e}")
        return None

# ==================== PLAYLIST generation ====================

async def generate_playlist():
    matches = parse_matches()
    if not matches:
        log.warning("No matches to process.")
        return "#EXTM3U\n"

    lines = ["#EXTM3U"]
    success = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="chrome-beta")
        ctx = await browser.new_context(user_agent=USER_AGENT, extra_http_headers=CUSTOM_HEADERS, ignore_https_errors=True)

        for idx, m in enumerate(matches, 1):
            title = m["match"]
            league = m["league"]
            logo = m["logo"]
            iframe = m["iframe"]
            log.info(f"\n🎯 [{idx}/{len(matches)}] {league} — {title} → {iframe}")

            page = await ctx.new_page()
            m3u8 = await extract_m3u8(page, iframe)
            await page.close()

            if not m3u8:
                log.warning(f"❌ No m3u8 detected for {title}")
                continue

            tv_id = TV_IDS.get(league.lower(), TV_IDS["other"])
            lines.append(
                f'#EXTINF:-1 tvg-id="{tv_id}" tvg-name="{title}" tvg-logo="{logo}" group-title="StreamBTW - {league}",{title}'
            )
            lines.append(m3u8)
            success += 1

        await browser.close()

    log.info(f"\n🎉 {success} working streams written.")
    return "\n".join(lines)

# ==================== MAIN ====================

if __name__ == "__main__":
    start = datetime.now()
    log.info("🚀 Starting StreamBTW scraper...")
    playlist = asyncio.run(generate_playlist())
    with open(PLAYLIST_FILE, "w", encoding="utf-8") as fh:
        fh.write(playlist)
    elapsed = (datetime.now() - start).total_seconds()
    log.info(f"Completed in {elapsed:.2f}s")