import os
import json
import time
import re
import logging
import hashlib
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
TOPIC_ID = int(os.environ["TELEGRAM_TOPIC_ID"])

POLL_INTERVAL_SECONDS = 60
SEEN_FILE = Path(__file__).parent / "seen_articles.json"

TE_URL = "https://id.tradingeconomics.com/stream"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    "Referer": "https://id.tradingeconomics.com/",
}


def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text())
            return set(data)
        except Exception:
            return set()
    return set()


def save_seen(seen: set) -> None:
    items = list(seen)[-3000:]
    SEEN_FILE.write_text(json.dumps(items))


def make_id(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


def send_message(text: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "message_thread_id": TOPIC_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("Failed to send: %s", e)
        return False


def scrape_te() -> list:
    try:
        resp = requests.get(TE_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        items = []
        seen_ids = set()

        # Cari semua artikel
        articles = (
            soup.find_all("div", class_=re.compile(r"stream|news|article|story|post", re.I)) +
            soup.find_all("article") +
            soup.find_all("li", class_=re.compile(r"stream|news|item", re.I))
        )

        # Fallback: cari semua link berita
        if not articles:
            links = soup.find_all("a", href=True)
            for link in links:
                title = link.get_text(strip=True)
                href = link.get("href", "")
                if len(title) < 15 or len(title) > 300:
                    continue
                if not href.startswith("http"):
                    href = "https://id.tradingeconomics.com" + href
                item_id = make_id(title)
                if item_id not in seen_ids:
                    seen_ids.add(item_id)
                    items.append({
                        "title": title,
                        "summary": "",
                        "link": href,
                        "id": item_id
                    })
            return items

        for article in articles:
            title_el = (
                article.find(["h1", "h2", "h3", "h4"]) or
                article.find("a") or
                article
            )
            title = title_el.get_text(strip=True) if title_el else ""
            title = re.sub(r"\s+", " ", title).strip()

            if len(title) < 15 or len(title) > 300:
                continue

            link_el = article.find("a")
            link = ""
            if link_el:
                link = link_el.get("href", "")
                if link and not link.startswith("http"):
                    link = "https://id.tradingeconomics.com" + link

            p = article.find("p")
            summary = p.get_text(strip=True) if p else ""
            summary = re.sub(r"\s+", " ", summary)[:400]

            item_id = make_id(title + link)
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)

            items.append({
                "title": title,
                "summary": summary,
                "link": link,
                "id": item_id
            })

        log.info("Scraped %d items", len(items))
        return items

    except Exception as e:
        log.error("Scraping error: %s", e)
        return []


def format_message(title: str, summary: str, link: str) -> str:
    now_wib = datetime.now().strftime("%H:%M WIB")

    msg = f"⚡ <b>FAST NEWS</b>\n"
    msg += f"━━━━━━━━━━━━━━━━\n"
    msg += f"📰 <b>{title}</b>\n"

    if summary and len(summary) > 20:
        msg += f"\n{summary}\n"

    if link:
        msg += f"\n🔗 <a href='{link}'>Baca selengkapnya</a>\n"

    msg += f"━━━━━━━━━━━━━━━━\n"
    msg += f"📡 Trading Economics  |  🕐 {now_wib}"

    return msg


def fetch_and_post(seen: set) -> set:
    new_seen = set(seen)
    items = scrape_te()
    posted = 0

    for item in items[:30]:
        if item["id"] in new_seen:
            continue

        msg = format_message(item["title"], item["summary"], item["link"])

        if send_message(msg):
            new_seen.add(item["id"])
            posted += 1
            log.info("Posted: %s", item["title"][:80])
            time.sleep(2)

        if posted >= 5:
            break

    log.info("Posted %d items this round", posted)
    return new_seen


def main():
    log.info("🚀 AI KUTAN - Trading Economics Bot (No Filter)")
    log.info("Polling every %ds", POLL_INTERVAL_SECONDS)

    seen = load_seen()
    log.info("Loaded %d seen items", len(seen))

    while True:
        try:
            seen = fetch_and_post(seen)
            save_seen(seen)
        except Exception as e:
            log.error("Main loop error: %s", e, exc_info=True)

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
