import requests
import os
import html
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import unquote
from datetime import datetime, timezone

# ============================================================
# CONFIGURATION
# ============================================================
DISCORD_WEBHOOK = os.environ.get('DISCORD_WEBHOOK')
# Optional: a separate webhook for health alerts. Falls back to main webhook.
HEALTH_WEBHOOK = os.environ.get('HEALTH_WEBHOOK', DISCORD_WEBHOOK)

USERNAME = "FabrizioRomano"
KEYWORDS = ["here we go", "done deal", "medical booked", "medical scheduled"]
POSTED_FILE = "posted_ids.txt"
HEALTH_FILE = "health_state.txt"
TWEETS_TO_CHECK = 20

# --- Safety / behavior settings ---
DRY_RUN = False              # True = test mode, prints instead of posting
MAX_POSTS_PER_RUN = 5        # hard cap so it can never spam
POST_DELAY_SECONDS = 1       # wait between posts (avoid Discord rate limits)
PROXIMITY_LIMIT = 100        # club name must be within N chars of keyword

# --- Trim settings ---
TRIM_THRESHOLD = 100
TRIM_KEEP = 80

# --- #3 Money detection ---
BIG_MONEY_THRESHOLD = 40     # deals >= this many millions get the gold treatment

# --- #9 Health check ---
ALERT_AFTER_FAILURES = 5     # alert after this many consecutive total failures

# --- #13 Styling ---
FABRIZIO_AVATAR = "https://pbs.twimg.com/profile_images/874276197357596672/kUuht00m_400x400.jpg"

# ============================================================
# CLUB -> PING MAPPING
# "keywords": phrases that identify the club
# "pings": list of {"id": ..., "type": "user" or "role"}
# "crest": image URL shown as embed thumbnail (#13)
# ============================================================
CLUB_ROLES = {
    "Man UTD": {
        "keywords": ["manchester united", "man united", "man utd", "man u"],
        "pings": [
            {"id": "901476549964988467", "type": "user"}
        ],
        "crest": "https://a.espncdn.com/i/teamlogos/soccer/500/360.png"
    },
    "Real Madrid": {
        "keywords": ["real madrid"],
        "pings": [
            {"id": "1054525567267000320", "type": "user"},
            {"id": "1427048967698387035", "type": "user"}
        ],
        "crest": "https://a.espncdn.com/i/teamlogos/soccer/500/86.png"
    },
    "FC Barcelona": {
        "keywords": ["barcelona", "barca", "fc barcelona"],
        "pings": [
            {"id": "1449550570330521791", "type": "user"},
            {"id": "1293909882675920906", "type": "user"}
        ],
        "crest": "https://a.espncdn.com/i/teamlogos/soccer/500/83.png"
    },
    "Liverpool": {
        "keywords": ["liverpool"],
        "pings": [
            {"id": "899442380816670771", "type": "user"}
        ],
        "crest": "https://a.espncdn.com/i/teamlogos/soccer/500/364.png"
    },
    # Add more clubs here later
}

NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.poast.org",
    "https://nitter.tiekoetter.com",
    "https://lightbrd.com",
    "https://nitter.privacyredirect.com",
    "https://xcancel.com",
    "https://nitter.space",
    "https://nitter.kuuro.net"
]


# ============================================================
# STATE (posted IDs) HANDLING
# ============================================================
def load_posted_ids():
    if os.path.exists(POSTED_FILE):
        with open(POSTED_FILE, "r") as f:
            return [line.strip() for line in f if line.strip()]
    return []


def save_posted_ids(id_list):
    if len(id_list) > TRIM_THRESHOLD:
        id_list = id_list[-TRIM_KEEP:]
        print(f"Trimmed posted_ids down to last {TRIM_KEEP} entries.")
    with open(POSTED_FILE, "w") as f:
        f.write("\n".join(id_list) + "\n")


def normalize_id(raw_id, link):
    """Extract just the numeric tweet ID so it's identical across all
    Nitter instances (prevents duplicate posts)."""
    for source in (raw_id, link):
        if source:
            m = re.search(r'status/(\d+)', source)
            if m:
                return m.group(1)
    return raw_id


# ============================================================
# #9 HEALTH CHECK STATE
# ============================================================
def load_failure_count():
    if os.path.exists(HEALTH_FILE):
        try:
            with open(HEALTH_FILE, "r") as f:
                return int(f.read().strip() or "0")
        except ValueError:
            return 0
    return 0


def save_failure_count(count):
    with open(HEALTH_FILE, "w") as f:
        f.write(str(count))


def send_health_alert(message):
    payload = {
        "content": f"⚠️ **BOT HEALTH ALERT** ⚠️\n{message}",
        "username": "Bot Health Monitor"
    }
    try:
        requests.post(HEALTH_WEBHOOK, json=payload, timeout=15)
        print("Health alert sent.")
    except Exception as e:
        print(f"Could not send health alert: {e}")


# ============================================================
# #15 VIDEO + #13 IMAGE EXTRACTION
# ============================================================
def extract_media(description, base_url):
    """Return (image_url, has_video). Handles images and detects videos/GIFs."""
    image_url = None
    has_video = False

    if not description:
        return image_url, has_video

    # Detect video (#15): Nitter uses <video> tags or /video/ or .mp4 links
    if re.search(r'<video', description) or re.search(r'\.mp4', description) \
            or "/video/" in description or "tw_video" in description:
        has_video = True

    # Try to find an image first
    img_match = re.search(r'<img[^>]+src="([^"]+)"', description)
    src = None
    if img_match:
        src = img_match.group(1)
    else:
        # If no <img>, a video often has a poster="..." thumbnail
        poster_match = re.search(r'poster="([^"]+)"', description)
        if poster_match:
            src = poster_match.group(1)
            has_video = True

    if src:
        img_url = html.unescape(src)
        if img_url.startswith("/"):
            img_url = base_url.rstrip("/") + img_url

        pic_match = re.search(r'/pic/(?:orig/)?(.+)$', img_url)
        if pic_match:
            encoded_path = pic_match.group(1).split("?")[0]
            decoded_path = unquote(encoded_path)
            img_url = "https://pbs.twimg.com/" + decoded_path
            print(f"     Converted media img -> {img_url}")

    return img_url, has_video


# ============================================================
# #3 MONEY DETECTION
# ============================================================
def detect_money(text):
    """Find a transfer fee in the text. Returns (display_string, value_millions)
    or (None, 0) if none found."""
    # Matches €100m, £50 million, $75m, €100M, €1.2bn, etc.
    pattern = r'([€£$])\s?(\d+(?:\.\d+)?)\s?(m|million|bn|billion|k)\b'
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None, 0

    symbol = match.group(1)
    amount = float(match.group(2))
    unit = match.group(3).lower()

    if unit in ("bn", "billion"):
        millions = amount * 1000
        display = f"{symbol}{amount:g}bn"
    elif unit == "k":
        millions = amount / 1000
        display = f"{symbol}{amount:g}k"
    else:
        millions = amount
        display = f"{symbol}{amount:g}m"

    return display, millions


# ============================================================
# PING LOGIC (with proximity check) + club match for crest
# ============================================================
def get_club_matches(text_lower):
    """Return list of matched club dicts (with name, pings, crest),
    only if the club name is near a keyword phrase."""
    keyword_spans = []
    for kw in KEYWORDS:
        start = 0
        while True:
            idx = text_lower.find(kw, start)
            if idx == -1:
                break
            keyword_spans.append((idx, idx + len(kw)))
            start = idx + 1

    if not keyword_spans:
        return []

    matches = []
    for club_name, data in CLUB_ROLES.items():
        matched = False
        for kw in data["keywords"]:
            start = 0
            while not matched:
                idx = text_lower.find(kw, start)
                if idx == -1:
                    break
                club_start, club_end = idx, idx + len(kw)
                for (k_start, k_end) in keyword_spans:
                    gap = max(club_start, k_start) - min(club_end, k_end)
                    if gap <= PROXIMITY_LIMIT:
                        matched = True
                        break
                start = idx + 1
            if matched:
                break
        if matched:
            matches.append({"name": club_name, **data})
            print(f"     -> Club match (near keyword): {club_name}")
    return matches


def build_ping_string(matches):
    pings = []
    for data in matches:
        for p in data["pings"]:
            if p.get("type") == "user":
                pings.append(f"<@{p['id']}>")
            else:
                pings.append(f"<@&{p['id']}>")
    return " ".join(pings)


# ============================================================
# DISCORD POSTING (#13 styling, #3 money, #15 video)
# ============================================================
def send_to_discord(link, content, image_url=None, ping="",
                    crest=None, money_display=None, is_big_money=False,
                    has_video=False):
    if DRY_RUN:
        flags = []
        if ping: flags.append("ping")
        if is_big_money: flags.append(f"BIG MONEY {money_display}")
        elif money_display: flags.append(f"fee {money_display}")
        if has_video: flags.append("video")
        print(f"     [DRY RUN] Would post ({', '.join(flags) or 'plain'}): {content[:70]}")
        return True

    # Color priority: gold for big money, red for ping, blue otherwise
    if is_big_money:
        color = 0xFFD700  # gold
    elif ping:
        color = 0xDA020E  # red
    else:
        color = 0x1DA1F2  # blue

    # Title/author (#13)
    author_name = "🚨 HERE WE GO! 🚨"
    if is_big_money:
        author_name = f"💰 BIG MONEY MOVE — {money_display} 💰"

    embed = {
        "description": content,
        "color": color,
        "url": link,
        "author": {"name": author_name, "icon_url": FABRIZIO_AVATAR},
        "footer": {"text": "Fabrizio Romano", "icon_url": FABRIZIO_AVATAR},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    # Club crest thumbnail (#13)
    if crest:
        embed["thumbnail"] = {"url": crest}

    # Main image (#13)
    if image_url:
        embed["image"] = {"url": image_url}

    # Extra fields
    fields = []
    if money_display:
        fields.append({"name": "💵 Fee", "value": money_display, "inline": True})
    if has_video:
        fields.append({"name": "🎥 Media", "value": f"[Video/GIF — watch on X]({link})", "inline": True})
    if fields:
        embed["fields"] = fields

    # Message content with ping
    if ping:
        msg_content = f"{ping} 🔥 [Read on X]({link})"
    else:
        msg_content = f"[Read on X]({link})"

    payload = {
        "content": msg_content,
        "username": "Lutay FootBot",
        "embeds": [embed],
        "allowed_mentions": {"parse": ["roles", "users"]}
    }

    try:
        resp = requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
        print(f"     Discord response: {resp.status_code}")
        if resp.status_code == 429:
            retry = resp.json().get("retry_after", 2)
            print(f"     Rate limited, waiting {retry}s and retrying once...")
            time.sleep(float(retry) + 0.5)
            resp = requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
            print(f"     Retry response: {resp.status_code}")
        return resp.status_code in (200, 204)
    except Exception as e:
        print(f"     Discord error: {e}")
        return False


# ============================================================
# RSS FETCHING
# ============================================================
def fetch_rss():
    headers = {'User-Agent': 'Mozilla/5.0'}
    for base in NITTER_INSTANCES:
        url = f"{base}/{USERNAME}/rss"
        try:
            print(f"Trying: {url}")
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200 and "<item>" in response.text:
                print(f"✅ Success with {base}")
                return response.text, base
            else:
                print(f"❌ {base} returned status {response.status_code} or no items")
        except Exception as e:
            print(f"❌ {base} failed: {e}")
    return None, None


# ============================================================
# MAIN
# ============================================================
def main():
    rss_data, base_url = fetch_rss()

    # --- #9 Health check handling ---
    failures = load_failure_count()
    if not rss_data:
        failures += 1
        save_failure_count(failures)
        print(f"All Nitter instances failed. Consecutive failures: {failures}")
        if failures == ALERT_AFTER_FAILURES:
            send_health_alert(
                f"All Nitter instances have failed **{failures} times in a row**. "
                f"The bot can't fetch tweets right now. The instance list may need updating."
            )
        return
    else:
        # Recovered?
        if failures >= ALERT_AFTER_FAILURES:
            send_health_alert("✅ Recovered! Nitter is reachable again and the bot is back online.")
        if failures != 0:
            save_failure_count(0)

    try:
        root = ET.fromstring(rss_data)
    except ET.ParseError as e:
        print(f"XML parse error: {e}")
        return

    items = root.findall(".//item")
    if not items:
        print("No <item> elements found.")
        return

    posted_ids = load_posted_ids()
    posted_set = set(posted_ids)
    posts_this_run = 0

    print(f"\nLoaded {len(posted_ids)} previously posted IDs.")
    print(f"Found {len(items)} tweets in feed. Checking latest {TWEETS_TO_CHECK}.")
    if DRY_RUN:
        print("⚠️  DRY_RUN is ON — nothing will actually be posted.")
    print("=" * 60)

    recent_items = items[:TWEETS_TO_CHECK]

    for i, item in enumerate(reversed(recent_items)):
        title_el = item.find("title")
        link_el = item.find("link")
        guid_el = item.find("guid")
        desc_el = item.find("description")

        tweet_text = html.unescape(title_el.text) if title_el is not None and title_el.text else ""
        tweet_link = link_el.text.strip() if link_el is not None and link_el.text else ""
        raw_id = guid_el.text.strip() if guid_el is not None and guid_el.text else tweet_link
        tweet_id = normalize_id(raw_id, tweet_link)
        description = desc_el.text if desc_el is not None and desc_el.text else ""

        if not tweet_text or not tweet_id:
            print(f"[{i}] Empty fields, skipping.")
            continue

        already_seen = tweet_id in posted_set
        text_lower = tweet_text.lower()
        has_keyword = any(kw in text_lower for kw in KEYWORDS)

        print(f"[{i}] TEXT: {tweet_text[:100]}")
        print(f"     Already seen? {already_seen} | Has keyword? {has_keyword}")

        if already_seen:
            print("     -> SKIP (already posted)\n")
            continue

        if has_keyword:
            if posts_this_run >= MAX_POSTS_PER_RUN:
                print("     -> Reached MAX_POSTS_PER_RUN, marking seen & skipping.\n")
                posted_ids.append(tweet_id)
                posted_set.add(tweet_id)
                continue

            image_url, has_video = extract_media(description, base_url)
            matches = get_club_matches(text_lower)
            ping = build_ping_string(matches)
            crest = matches[0]["crest"] if matches else None

            money_display, money_value = detect_money(tweet_text)
            is_big_money = money_value >= BIG_MONEY_THRESHOLD

            print(f"     -> MATCH! Ping: {ping or 'none'} | Money: {money_display or 'none'} "
                  f"| Big: {is_big_money} | Video: {has_video} | Image: {image_url}")
            print("     -> Sending to Discord...")

            success = send_to_discord(
                tweet_link, tweet_text, image_url, ping,
                crest=crest, money_display=money_display,
                is_big_money=is_big_money, has_video=has_video
            )

            if success:
                posts_this_run += 1
                posted_ids.append(tweet_id)
                posted_set.add(tweet_id)
                print(f"     -> Posted OK ({posts_this_run}/{MAX_POSTS_PER_RUN})\n")
                time.sleep(POST_DELAY_SECONDS)
            else:
                print("     -> Discord FAILED, will retry next run.\n")
        else:
            print("     -> No keyword match.\n")
            posted_ids.append(tweet_id)
            posted_set.add(tweet_id)

    save_posted_ids(posted_ids)
    print("=" * 60)
    print(f"Done. Posted {posts_this_run} this run. Storing {len(posted_ids)} IDs.")


if __name__ == "__main__":
    main()
