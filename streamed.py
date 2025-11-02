import asyncio
import re
import requests
from playwright.async_api import async_playwright

CUSTOM_HEADERS = {
    "Origin": "https://embedsports.top",
    "Referer": "https://embedsports.top/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:141.0) Gecko/20100101 Firefox/141.0"
}

FALLBACK_LOGOS = {
    "american-football": "http://drewlive24.duckdns.org:9000/Logos/Am-Football2.png",
    "football": "https://external-content.duckduckgo.com/iu/?u=https://i.imgur.com/RvN0XSF.png",
    "fight": "http://drewlive24.duckdns.org:9000/Logos/Combat-Sports.png",
    "basketball": "http://drewlive24.duckdns.org:9000/Logos/Basketball5.png",
    "motor sports": "http://drewlive24.duckdns.org:9000/Logos/Motorsports3.png",
    "darts": "http://drewlive24.duckdns.org:9000/Logos/Darts.png"
}

TV_IDS = {
    "Baseball": "MLB.Baseball.Dummy.us",
    "Fight": "PPV.EVENTS.Dummy.us",
    "American Football": "NFL.Dummy.us",
    "Afl": "AUS.Rules.Football.Dummy.us",
    "Football": "Soccer.Dummy.us",
    "Basketball": "Basketball.Dummy.us",
    "Hockey": "NHL.Hockey.Dummy.us",
    "Tennis": "Tennis.Dummy.us",
    "Darts": "Darts.Dummy.us",
    "Motor Sports": "Racing.Dummy.us"
}

def get_all_matches():
    endpoints = ["all", "live", "today", "upcoming"]
    all_matches = []
    for ep in endpoints:
        try:
            print(f"📡 Fetching matches from endpoint: {ep}")
            res = requests.get(f"https://streami.su/api/matches/{ep}", timeout=15)
            res.raise_for_status()
            data = res.json()
            print(f"✅ Got {len(data)} matches from {ep}")
            all_matches.extend(data)
        except Exception as e:
            print(f"⚠️ Failed fetching {ep}: {e}")
    print(f"🎯 Total matches collected: {len(all_matches)}")
    return all_matches


def get_embed_urls_from_api(source):
    try:
        s_name, s_id = source.get("source"), source.get("id")
        if not s_name or not s_id:
            return []
        api_url = f"https://streami.su/api/stream/{s_name}/{s_id}"
        res = requests.get(api_url, timeout=10)
        res.raise_for_status()
        data = res.json()
        return [d.get("embedUrl") for d in data if d.get("embedUrl")]
    except:
        return []


async def extract_m3u8(embed_url, timeout=10000):
    found = None
    try:
        async with async_playwright() as p:
            browser = await p.firefox.launch(headless=True)
            ctx = await browser.new_context(extra_http_headers=CUSTOM_HEADERS)
            page = await ctx.new_page()

            async def on_request(request):
                nonlocal found
                if ".m3u8" in request.url and not found:
                    found = request.url
                    print(f"  ⚡ Found stream → {found}")

            page.on("request", on_request)

            await page.goto(embed_url, wait_until="domcontentloaded", timeout=timeout)

            selectors = [
                "div.jw-icon-display[role='button']",
                ".jw-icon-playback",
                ".vjs-big-play-button",
                ".plyr__control",
                "div[class*='play']",
                "div[role='button']",
                "button",
                "canvas"
            ]

            for _ in range(3):
                for sel in selectors:
                    try:
                        el = await page.query_selector(sel)
                        if el:
                            await el.click(timeout=500)
                            break
                    except:
                        continue
                if found:
                    break
                await asyncio.sleep(0.25)

            for _ in range(3):
                if found:
                    break
                await asyncio.sleep(0.5)

            if not found:
                html = await page.content()
                matches = re.findall(r'https?://[^\s"\'<>]+\.m3u8(?:\?[^"\'<>]*)?', html)
                if matches:
                    found = matches[0]
                    print(f"  🕵️ Fallback found → {found}")

            await browser.close()
            return found
    except Exception as e:
        print(f"⚠️ {embed_url} failed: {e}")
        return None


def validate_logo(url, category):
    cat = (category or "").lower().replace("-", " ").strip()
    fallback = FALLBACK_LOGOS.get(cat)
    if url:
        try:
            res = requests.head(url, timeout=5)
            if res.status_code in (200, 302):
                return url
        except:
            pass
    return fallback


def build_logo_url(match):
    cat = (match.get("category") or "").strip()
    teams = match.get("teams") or {}
    for side in ["away", "home"]:
        badge = teams.get(side, {}).get("badge")
        if badge:
            url = f"https://streamed.pk/api/images/badge/{badge}.webp"
            return validate_logo(url, cat), cat
    if match.get("poster"):
        url = f"https://streamed.pk/api/images/proxy/{match['poster']}.webp"
        return validate_logo(url, cat), cat
    return validate_logo(None, cat), cat


async def process_match(match):
    title = match.get("title", "Unknown Match")
    sources = match.get("sources", [])
    for s in sources:
        embed_urls = get_embed_urls_from_api(s)
        for embed in embed_urls:
            print(f"\n  🔎 Checking '{title}': {embed}")
            m3u8 = await extract_m3u8(embed)
            if m3u8:
                print(f"  ✅ Found stream for {title}: {m3u8}")
                return match, m3u8
    return match, None


async def generate_playlist():
    matches = get_all_matches()
    if not matches:
        print("❌ No matches found.")
        return "#EXTM3U\n"

    content = ["#EXTM3U"]
    success = 0

    for match in matches:
        match, url = await process_match(match)
        if not url:
            continue
        logo, cat = build_logo_url(match)
        title = match.get("title", "Untitled")
        display_cat = cat.replace("-", " ").title() if cat else "General"
        tv_id = TV_IDS.get(display_cat, "General.Dummy.us")

        content.append(f'#EXTINF:-1 tvg-id="{tv_id}" tvg-name="{title}" tvg-logo="{logo}" group-title="StreamedSU - {display_cat}",{title}')
        content.append(f'#EXTVLCOPT:http-origin={CUSTOM_HEADERS["Origin"]}')
        content.append(f'#EXTVLCOPT:http-referrer={CUSTOM_HEADERS["Referer"]}')
        content.append(f'#EXTVLCOPT:user-agent={CUSTOM_HEADERS["User-Agent"]}')
        content.append(url)
        success += 1

    print(f"🎉 {success} working streams found.")
    return "\n".join(content)


if __name__ == "__main__":
    playlist = asyncio.run(generate_playlist())
    with open("StreamedSU.m3u8", "w", encoding="utf-8") as f:
        f.write(playlist)
    print("💾 Saved as StreamedSU.m3u8")
