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

POLL_INTERVAL_SECONDS = 30  # cek setiap 30 detik = real-time
SEEN_FILE = Path(__file__).parent / "seen_articles.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.financialjuice.com/",
}

FJ_URL = "https://www.financialjuice.com/home"


def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text())
            return set(data)
        except Exception:
            return set()
    return set()


def save_seen(seen: set) -> None:
    items = list(seen)[-2000:]
    SEEN_FILE.write_text(json.dumps(items))


def make_id(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


def translate_to_indonesian(text: str) -> str:
    try:
        url = "https://api.mymemory.translated.net/get"
        params = {"q": text[:400], "langpair": "en|id"}
        resp = requests.get(url, params=params, timeout=8)
        data = resp.json()
        translated = data["responseData"]["translatedText"]
        if translated and len(translated) > 5:
            return translated
    except Exception as e:
        log.warning("Translation failed: %s", e)
    return text


def send_text(text: str) -> bool:
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
        log.error("Failed to send text: %s", e)
        return False


def send_photo(image_url: str, caption: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    payload = {
        "chat_id": CHAT_ID,
        "message_thread_id": TOPIC_ID,
        "photo": image_url,
        "caption": caption,
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.warning("Failed to send photo, sending text instead: %s", e)
        return False


def scrape_financial_juice() -> list:
    """Scrape high impact (red) news from Financial Juice"""
    try:
        resp = requests.get(FJ_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        news_items = []

        # Cari semua news item
        # Financial Juice menggunakan class tertentu untuk high impact
        all_items = soup.find_all(["div", "li", "article"], class_=re.compile(
            r"(high|red|important|breaking|alert|news-item|feed-item|story)", re.I
        ))

        # Juga cari berdasarkan style/color merah
        red_items = soup.find_all(lambda tag: tag.get("style") and (
            "red" in tag.get("style", "").lower() or
            "#ff" in tag.get("style", "").lower() or
            "color: red" in tag.get("style", "").lower()
        ))

        all_items.extend(red_items)

        # Cari semua paragraf/div yang mengandung teks berita
        all_news = soup.find_all(["div", "p", "span"], class_=re.compile(
            r"(news|feed|headline|story|item|post|content)", re.I
        ))

        for item in all_news:
            text = item.get_text(strip=True)
            if len(text) < 20 or len(text) > 500:
                continue

            # Cek apakah ini high impact berdasarkan parent class atau style
            parent = item.parent
            is_high = False

            # Cek class high impact
            classes = " ".join(item.get("class", []) + (parent.get("class", []) if parent else []))
            if re.search(r"(high|red|important|breaking|alert|priority)", classes, re.I):
                is_high = True

            # Cek style merah
            style = item.get("style", "") + (parent.get("style", "") if parent else "")
            if "red" in style.lower() or "#f00" in style.lower() or "#ff0000" in style.lower():
                is_high = True

            # Cek data attributes
            data_attrs = str(item.attrs)
            if re.search(r"(high|red|important|1)", data_attrs, re.I):
                is_high = True

            if not is_high:
                continue

            # Ambil gambar jika ada
            img = item.find("img") or (parent.find("img") if parent else None)
            img_url = None
            if img:
                img_url = img.get("src") or img.get("data-src")
                if img_url and not img_url.startswith("http"):
                    img_url = "https://www.financialjuice.com" + img_url

            news_items.append({
                "text": text,
                "img_url": img_url,
                "id": make_id(text)
            })

        # Deduplicate
        seen_ids = set()
        unique_items = []
        for item in news_items:
            if item["id"] not in seen_ids:
                seen_ids.add(item["id"])
                unique_items.append(item)

        log.info("Found %d high impact items from Financial Juice", len(unique_items))
        return unique_items

    except Exception as e:
        log.error("Error scraping Financial Juice: %s", e)
        return []


def format_message(text: str) -> str:
    now_wib = datetime.now().strftime("%H:%M WIB")
    translated = translate_to_indonesian(text)

    msg = (
        f"🔴 <b>FINANCIAL JUICE — HIGH IMPACT</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⚡ {translated}\n"
        f"\n"
        f"🌐 <i>{text}</i>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📡 Financial Juice  |  🕐 {now_wib}"
    )
    return msg


def fetch_and_post(seen: set) -> set:
    new_seen = set(seen)
    items = scrape_financial_juice()

    posted = 0
    for item in items:
        if item["id"] in new_seen:
            continue

        msg = format_message(item["text"])

        if item["img_url"]:
            # Kirim dengan gambar
            success = send_photo(item["img_url"], msg)
            if not success:
                success = send_text(msg)
        else:
            success = send_text(msg)

        if success:
            new_seen.add(item["id"])
            posted += 1
            log.info("Posted: %s", item["text"][:80])
            time.sleep(1)

    if posted:
        log.info("Posted %d new high impact items", posted)
    else:
        log.info("No new high impact items")

    return new_seen


def main():
    log.info("🔴 AI KUTAN - Financial Juice Real-Time Bot Starting...")
    log.info("Checking every %ds for high impact news", POLL_INTERVAL_SECONDS)

    seen = load_seen()
    log.info("Loaded %d previously seen items", len(seen))

    while True:
        try:
            seen = fetch_and_post(seen)
            save_seen(seen)
        except Exception as e:
            log.error("Unexpected error: %s", e, exc_info=True)

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text())
            return set(data)
        except Exception:
            return set()
    return set()


def save_seen(seen: set) -> None:
    items = list(seen)[-2000:]
    SEEN_FILE.write_text(json.dumps(items))


def make_id(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


def translate_to_indonesian(text: str) -> str:
    try:
        url = "https://api.mymemory.translated.net/get"
        params = {"q": text[:400], "langpair": "en|id"}
        resp = requests.get(url, params=params, timeout=8)
        data = resp.json()
        translated = data["responseData"]["translatedText"]
        if translated and len(translated) > 5:
            return translated
    except Exception as e:
        log.warning("Translation failed: %s", e)
    return text


def send_text(text: str) -> bool:
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
        log.error("Failed to send text: %s", e)
        return False


def send_photo(image_url: str, caption: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    payload = {
        "chat_id": CHAT_ID,
        "message_thread_id": TOPIC_ID,
        "photo": image_url,
        "caption": caption,
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.warning("Failed to send photo, sending text instead: %s", e)
        return False


def scrape_financial_juice() -> list:
    """Scrape high impact (red) news from Financial Juice"""
    try:
        resp = requests.get(FJ_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        news_items = []

        # Cari semua news item
        # Financial Juice menggunakan class tertentu untuk high impact
        all_items = soup.find_all(["div", "li", "article"], class_=re.compile(
            r"(high|red|important|breaking|alert|news-item|feed-item|story)", re.I
        ))

        # Juga cari berdasarkan style/color merah
        red_items = soup.find_all(lambda tag: tag.get("style") and (
            "red" in tag.get("style", "").lower() or
            "#ff" in tag.get("style", "").lower() or
            "color: red" in tag.get("style", "").lower()
        ))

        all_items.extend(red_items)

        # Cari semua paragraf/div yang mengandung teks berita
        all_news = soup.find_all(["div", "p", "span"], class_=re.compile(
            r"(news|feed|headline|story|item|post|content)", re.I
        ))

        for item in all_news:
            text = item.get_text(strip=True)
            if len(text) < 20 or len(text) > 500:
                continue

            # Cek apakah ini high impact berdasarkan parent class atau style
            parent = item.parent
            is_high = False

            # Cek class high impact
            classes = " ".join(item.get("class", []) + (parent.get("class", []) if parent else []))
            if re.search(r"(high|red|important|breaking|alert|priority)", classes, re.I):
                is_high = True

            # Cek style merah
            style = item.get("style", "") + (parent.get("style", "") if parent else "")
            if "red" in style.lower() or "#f00" in style.lower() or "#ff0000" in style.lower():
                is_high = True

            # Cek data attributes
            data_attrs = str(item.attrs)
            if re.search(r"(high|red|important|1)", data_attrs, re.I):
                is_high = True

            if not is_high:
                continue

            # Ambil gambar jika ada
            img = item.find("img") or (parent.find("img") if parent else None)
            img_url = None
            if img:
                img_url = img.get("src") or img.get("data-src")
                if img_url and not img_url.startswith("http"):
                    img_url = "https://www.financialjuice.com" + img_url

            news_items.append({
                "text": text,
                "img_url": img_url,
                "id": make_id(text)
            })

        # Deduplicate
        seen_ids = set()
        unique_items = []
        for item in news_items:
            if item["id"] not in seen_ids:
                seen_ids.add(item["id"])
                unique_items.append(item)

        log.info("Found %d high impact items from Financial Juice", len(unique_items))
        return unique_items

    except Exception as e:
        log.error("Error scraping Financial Juice: %s", e)
        return []


def format_message(text: str) -> str:
    now_wib = datetime.now().strftime("%H:%M WIB")
    translated = translate_to_indonesian(text)

    msg = (
        f"🔴 <b>FINANCIAL JUICE — HIGH IMPACT</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⚡ {translated}\n"
        f"\n"
        f"🌐 <i>{text}</i>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📡 Financial Juice  |  🕐 {now_wib}"
    )
    return msg


def fetch_and_post(seen: set) -> set:
    new_seen = set(seen)
    items = scrape_financial_juice()

    posted = 0
    for item in items:
        if item["id"] in new_seen:
            continue

        msg = format_message(item["text"])

        if item["img_url"]:
            # Kirim dengan gambar
            success = send_photo(item["img_url"], msg)
            if not success:
                success = send_text(msg)
        else:
            success = send_text(msg)

        if success:
            new_seen.add(item["id"])
            posted += 1
            log.info("Posted: %s", item["text"][:80])
            time.sleep(1)

    if posted:
        log.info("Posted %d new high impact items", posted)
    else:
        log.info("No new high impact items")

    return new_seen


def main():
    log.info("🔴 AI KUTAN - Financial Juice Real-Time Bot Starting...")
    log.info("Checking every %ds for high impact news", POLL_INTERVAL_SECONDS)

    seen = load_seen()
    log.info("Loaded %d previously seen items", len(seen))

    while True:
        try:
            seen = fetch_and_post(seen)
            save_seen(seen)
        except Exception as e:
            log.error("Unexpected error: %s", e, exc_info=True)

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
