import os
import json
import time
import re
import logging
import hashlib
from datetime import datetime
from pathlib import Path

import requests
import feedparser

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

# RSS Feed Trading Economics - Berita & Ekonomi
FEEDS = [
    {
        "name": "Trading Economics",
        "url": "https://tradingeconomics.com/rss/news.aspx",
    },
    {
        "name": "Trading Economics Markets",
        "url": "https://tradingeconomics.com/rss/markets.aspx",
    },
]

# Keyword filter - hanya berita yang relevan untuk trader
HIGH_IMPACT_KEYWORDS = [
    # Bank Sentral
    "fed", "federal reserve", "fomc", "powell", "suku bunga", "interest rate",
    "rate hike", "rate cut", "pivot", "bank sentral", "central bank",
    "ecb", "boj", "bank of japan", "bank of england", "boe",
    # Data Ekonomi
    "inflation", "inflasi", "cpi", "pce", "ppi",
    "gdp", "pertumbuhan ekonomi", "economic growth", "recession", "resesi",
    "jobs", "unemployment", "pengangguran", "nonfarm", "payroll", "nfp",
    "retail sales", "penjualan ritel", "manufacturing", "pmi",
    # Geopolitik
    "geopolitik", "geopolitical", "war", "perang", "conflict", "konflik",
    "sanctions", "sanksi", "iran", "russia", "ukraine", "china", "taiwan",
    "timur tengah", "middle east", "nato",
    # Komoditas & Market
    "oil", "crude", "minyak", "opec",
    "gold", "emas", "silver", "perak",
    "dollar", "dxy", "rupiah",
    # Market Events
    "market crash", "stock market", "wall street", "bursa",
    "tariff", "trade war", "perang dagang",
    "debt", "default", "crisis", "krisis",
    # Obligasi
    "yield", "obligasi", "bond", "treasury",
]

# Keyword yang diabaikan
IGNORE_KEYWORDS = [
    "soccer", "football", "sport", "entertainment",
    "celebrity", "musik", "film",
]


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


def is_relevant(title: str, summary: str) -> bool:
    text = (title + " " + summary).lower()
    for bad in IGNORE_KEYWORDS:
        if bad in text:
            return False
    for kw in HIGH_IMPACT_KEYWORDS:
        if kw in text:
            return True
    return False


def translate_to_indonesian(text: str) -> str:
    """Translate menggunakan MyMemory API - GRATIS"""
    if not text or len(text) < 5:
        return text
    try:
        url = "https://api.mymemory.translated.net/get"
        params = {
            "q": text[:400],
            "langpair": "en|id",
            "de": "bot@aikutan.com"
        }
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        translated = data["responseData"]["translatedText"]
        if translated and len(translated) > 5 and "MYMEMORY WARNING" not in translated:
            return translated
    except Exception as e:
        log.warning("Translation failed: %s", e)
    return text


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


def clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def format_message(source: str, title: str, summary: str, link: str) -> str:
    now_wib = datetime.now().strftime("%H:%M WIB")

    # Translate judul
    title_id = translate_to_indonesian(title)
    time.sleep(0.5)

    # Translate summary kalau ada
    summary_id = ""
    if summary and len(summary) > 20:
        summary_clean = clean_html(summary)[:350]
        summary_id = translate_to_indonesian(summary_clean)

    msg = f"⚡ <b>FAST NEWS — HIGH IMPACT</b>\n"
    msg += f"━━━━━━━━━━━━━━━━\n"
    msg += f"📰 <b>{title_id}</b>\n"

    if summary_id:
        msg += f"\n{summary_id}\n"

    if link:
        msg += f"\n🔗 <a href='{link}'>Baca selengkapnya</a>\n"

    msg += f"━━━━━━━━━━━━━━━━\n"
    msg += f"📡 {source}  |  🕐 {now_wib}"

    return msg


def fetch_and_post(seen: set) -> set:
    new_seen = set(seen)
    total_posted = 0

    for feed_info in FEEDS:
        name = feed_info["name"]
        url = feed_info["url"]

        try:
            log.info("Fetching %s...", name)
            parsed = feedparser.parse(url)
            entries = parsed.entries or []
            log.info("%s: %d entries found", name, len(entries))
        except Exception as e:
            log.warning("Error fetching %s: %s", name, e)
            continue

        posted = 0
        for entry in entries[:20]:  # cek 20 terbaru
            title = getattr(entry, "title", "").strip()
            link = getattr(entry, "link", "").strip()
            summary = clean_html(getattr(entry, "summary", ""))

            if not title:
                continue

            item_id = make_id(title + link)
            if item_id in new_seen:
                continue

            # Filter relevan
            if not is_relevant(title, summary):
                new_seen.add(item_id)
                continue

            msg = format_message(name, title, summary, link)

            if send_message(msg):
                new_seen.add(item_id)
                posted += 1
                total_posted += 1
                log.info("Posted: %s", title[:80])
                time.sleep(2)

            if posted >= 3:  # max 3 per feed per run
                break

        log.info("%s: posted %d items", name, posted)

    log.info("Total posted this round: %d", total_posted)
    return new_seen


def main():
    log.info("🚀 AI KUTAN Fast News Bot - Trading Economics")
    log.info("Polling every %ds", POLL_INTERVAL_SECONDS)

    seen = load_seen()
    log.info("Loaded %d seen items", len(seen))

    while True:
        try:
            seen = fetch_and_post(seen)
            save_seen(seen)
        except Exception as e:
            log.error("Main loop error: %s", e, exc_info=True)

        log.info("Sleeping %ds...", POLL_INTERVAL_SECONDS)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
