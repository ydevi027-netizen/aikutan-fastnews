import os
import json
import time
import re
import logging
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
TOPIC_ID = int(os.environ["TELEGRAM_TOPIC_ID"])

POLL_INTERVAL_SECONDS = 300
MAX_ARTICLES_PER_RUN = 5
SEEN_FILE = Path(__file__).parent / "seen_articles.json"

# ✅ Keyword filter - hanya berita high impact
HIGH_IMPACT_KEYWORDS = [
    "fed", "federal reserve", "interest rate", "suku bunga",
    "inflation", "inflasi", "cpi", "pce",
    "gdp", "economic growth", "recession", "resesi",
    "jobs", "unemployment", "nonfarm", "payroll",
    "rate hike", "rate cut", "pivot",
    "powell", "fomc",
    "bank sentral", "central bank",
    "geopolitik", "geopolitical", "war", "perang", "sanctions",
    "china", "taiwan", "russia", "ukraine",
    "oil", "crude", "minyak", "opec",
    "gold", "emas", "dollar", "dxy",
    "market crash", "stock market", "wall street",
    "tariff", "trade war", "perang dagang",
    "debt ceiling", "default",
    "earnings beat", "earnings miss",
    "ipo", "merger", "acquisition",
]

# ❌ Keyword yang diabaikan
IGNORE_KEYWORDS = [
    "earnings call transcript",
    "podcast",
    "video:",
    "watch:",
    "quiz",
    "horoscope",
]

FEEDS = [
    {"name": "Reuters", "url": "https://feeds.reuters.com/reuters/businessNews"},
    {"name": "CNBC", "url": "https://www.cnbc.com/id/10000664/device/rss/rss.html"},
    {"name": "MarketWatch", "url": "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines"},
    {"name": "Investing.com", "url": "https://www.investing.com/rss/news.rss"},
]


def is_high_impact(title: str, summary: str) -> bool:
    text = (title + " " + summary).lower()
    for bad in IGNORE_KEYWORDS:
        if bad.lower() in text:
            return False
    for kw in HIGH_IMPACT_KEYWORDS:
        if kw.lower() in text:
            return True
    return False


def translate_to_indonesian(text: str) -> str:
    """Translate using MyMemory free API"""
    try:
        url = "https://api.mymemory.translated.net/get"
        params = {"q": text[:500], "langpair": "en|id"}
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        translated = data["responseData"]["translatedText"]
        if translated and len(translated) > 10:
            return translated
    except Exception as e:
        log.warning("Translation failed: %s", e)
    return text


def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text())
            return set(data)
        except Exception:
            return set()
    return set()


def save_seen(seen: set) -> None:
    items = list(seen)[-5000:]
    SEEN_FILE.write_text(json.dumps(items))


def article_id(entry) -> str:
    key = getattr(entry, "id", None) or getattr(entry, "link", "") or entry.get("title", "")
    return hashlib.sha256(key.encode()).hexdigest()


def send_message(text: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "message_thread_id": TOPIC_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.error("Failed to send message: %s", exc)
        return False


def format_article(source_name: str, entry) -> str:
    title_en = getattr(entry, "title", "No title").strip()
    link = getattr(entry, "link", "").strip()
    summary_en = getattr(entry, "summary", "").strip()

    # Bersihkan HTML
    summary_en = re.sub(r"<[^>]+>", "", summary_en)
    summary_en = summary_en[:300]

    # Translate judul dan summary
    title_id = translate_to_indonesian(title_en)
    summary_id = translate_to_indonesian(summary_en) if summary_en else ""

    # Waktu WIB
    now_wib = datetime.now().strftime("%H:%M WIB")

    # Format pesan
    msg = (
        f"⚡ <b>FAST NEWS — HIGH IMPACT</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📰 <b>{title_id}</b>\n"
    )
    if summary_id:
        msg += f"\n{summary_id}\n"

    msg += (
        f"\n🔗 <a href='{link}'>Baca selengkapnya</a>\n"
        f"📡 Sumber: {source_name}\n"
        f"🕐 {now_wib}"
    )

    return msg


def fetch_and_post(seen: set) -> set:
    new_seen = set(seen)
    for feed_info in FEEDS:
        name = feed_info["name"]
        url = feed_info["url"]
        try:
            log.info("Fetching %s ...", name)
            parsed = feedparser.parse(url)
            entries = parsed.entries or []
        except Exception as exc:
            log.warning("Error fetching %s: %s", name, exc)
            continue

        posted = 0
        for entry in entries:
            if posted >= MAX_ARTICLES_PER_RUN:
                break
            aid = article_id(entry)
            if aid in new_seen:
                continue

            title = getattr(entry, "title", "")
            summary = getattr(entry, "summary", "")
            summary = re.sub(r"<[^>]+>", "", summary)

            # Filter high impact
            if not is_high_impact(title, summary):
                new_seen.add(aid)  # mark seen tapi tidak dipost
                continue

            text = format_article(name, entry)
            if send_message(text):
                new_seen.add(aid)
                posted += 1
                log.info("Posted: %s", title)
                time.sleep(2)

        log.info("%s: posted %d high impact article(s)", name, posted)

    return new_seen


def main():
    log.info("🚀 AI KUTAN Fast News Bot starting...")
    log.info("Chat ID: %s | Topic ID: %d", CHAT_ID, TOPIC_ID)
    log.info("Checking %d feeds every %ds", len(FEEDS), POLL_INTERVAL_SECONDS)

    seen = load_seen()
    log.info("Loaded %d previously seen articles", len(seen))

    while True:
        try:
            seen = fetch_and_post(seen)
            save_seen(seen)
        except Exception as exc:
            log.error("Unexpected error: %s", exc, exc_info=True)

        log.info("Sleeping %ds until next check...", POLL_INTERVAL_SECONDS)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
