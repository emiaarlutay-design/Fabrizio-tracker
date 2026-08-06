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
USERNAME = "FabrizioRomano"
KEYWORDS = ["here we go", "done deal", "medical booked", "medical scheduled"]
POSTED_FILE = "posted_ids.txt"
TWEETS_TO_CHECK = 20

# --- Safety / behavior settings ---
DRY_RUN = False              # True = test mode, prints instead of posting
MAX_POSTS_PER_RUN = 5        # hard cap so it can never spam
POST_DELAY_SECONDS = 1       # wait between posts (avoid Discord rate limits)
PROXIMITY_LIMIT = 100        # club name must be within N chars of keyword

# --- Trim settings ---
TRIM_THRESHOLD = 100         # when file exceeds this many lines...
TRIM_KEEP = 80               # ...cut back to this many (keeps newest)

# ============================================================
# CLUB -> PING MAPPING
# "keywords": phrases that identify the club
# "pings": list of {"id": ..., "type": "user" or "role"}
# ============================================================
CLUB_ROLES = {
    "Man UTD": {
        "keywords": ["manchester united", "man united", "man utd", "man u"],
        "pings": [
            {"id": "901476549964988467", "type": "user"}
        ]
    },
    "Real Madrid": {
        "keywords": ["real madrid"],
        "pings": [
            {"id": "1054525567267000320", "type": "user"},
            {"id": "1427048967698387035", "type": "user"}
        ]
    },
    "FC Barcelona": {
        "keywords": ["barcelona", "barca", "fc barcelona"],
        "pings": [
            {"id": "1449550570330521791", "type": "user"},
            {"id": "1293909882675920906", "type": "user"}
        ]
    },
    "Liverpool": {
        "keywords": ["liverpool"],
        "pings": [
            {"id": "899442380816670771", "type": "user"}
        ]
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
    """Save IDs. Only trims when the list exceeds TRIM_THRESHOLD,
    then keeps the newest TRIM_KEEP entries."""
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
# IMAGE EXTRACTION
# ============================================================
def extract_image(description, base_url):
    if not description:
        return None

    match = re.search(r'<img[^>]+src="([^"]+)"', description)
    if not match:
        return None

    img_url = html.unescape(match.group(1))

    if img_url.startswith("/"):
        img_url = base_url.rstrip("/") + img_url

    pic_match = re.search(r'/pic/(?:orig/)?(.+)$', img_url)
    if pic_match:
        encoded_path = pic_match.group(1)
        encoded_path = encoded_path.split("?")[0]
        decoded_path = unquote(encoded_path)
        twitter_url = "https://pbs.twimg.com/" + decoded_path
        print(f"     Converted Nitter img -> {twitter_url}")
        return twitter_url

    return img_url


# ============================================================
# PING LOGIC (with proximity check)
# ============================================================
def get_ping(text_lower):
    """Ping a club ONLY if its name appears near a keyword phrase,
    so we don't ping a team just mentioned in passing."""
    # Find every position where a keyword phrase occurs
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
        return ""

    pings = []
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
            for p in data["pings"]:
                if p.get("type") == "user":
                    pings.append(f"<@{p['id']}>")
                else:
                    pings.append(f"<@&{p['id']}>")
            print(f"     -> Club match (near keyword): {club_name}")

    return " ".join(pings)


# ============================================================
# DISCORD POSTING
# ============================================================
def send_to_discord(link, content, image_url=None, ping=""):
    """Send a message. Returns True only if Discord confirms success."""
    if DRY_RUN:
        print(f"     [DRY RUN] Would post (ping={ping or 'none'}): {content[:70]}")
        return True

    embed = {
        "description": content,
        "color": 0xDA020E if ping else 0x1DA1F2,  # red if ping, else blue
        "url": link,
        "author": {"name": "🚨 HERE WE GO! 🚨"},
        "footer": {"text": "Fabrizio Romano"},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    if image_url:
        embed["image"] = {"url": image_url}

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
        # Handle rate limit explicitly
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
    if not rss_data:
        print("All Nitter instances failed. Exiting.")
        return

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
            # Anti-spam safety cap
            if posts_this_run >= MAX_POSTS_PER_RUN:
                print("     -> Reached MAX_POSTS_PER_RUN, marking seen & skipping.\n")
                posted_ids.append(tweet_id)
                posted_set.add(tweet_id)
                continue

            image_url = extract_image(description, base_url)
            ping = get_ping(text_lower)
            print(f"     -> MATCH! Ping: {ping if ping else 'none'} | Image: {image_url}")
            print("     -> Sending to Discord...")

            success = send_to_discord(tweet_link, tweet_text, image_url, ping)

            if success:
                posts_this_run += 1
                posted_ids.append(tweet_id)
                posted_set.add(tweet_id)
                print(f"     -> Posted OK ({posts_this_run}/{MAX_POSTS_PER_RUN})\n")
                time.sleep(POST_DELAY_SECONDS)  # avoid rate limits
            else:
                # Don't mark as seen -> will retry next run
                print("     -> Discord FAILED, will retry next run.\n")
        else:
            print("     -> No keyword match.\n")
            # Mark non-matching tweets as seen so we don't re-check forever
            posted_ids.append(tweet_id)
            posted_set.add(tweet_id)

    save_posted_ids(posted_ids)
    print("=" * 60)
    print(f"Done. Posted {posts_this_run} this run. Storing {len(posted_ids)} IDs.")


if __name__ == "__main__":
    main()
