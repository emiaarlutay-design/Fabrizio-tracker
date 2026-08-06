import requests
import os
import html
import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote

# CONFIGURATION
DISCORD_WEBHOOK = os.environ.get('DISCORD_WEBHOOK')
USERNAME = "FabrizioRomano"
KEYWORDS = ["here we go", "done deal", "medical booked", "medical scheduled"]
POSTED_FILE = "posted_ids.txt"
TWEETS_TO_CHECK = 20
MAX_STORED_IDS = 100

# --- CLUB -> PING MAPPING ---
# Each club has:
#   "keywords": phrases that identify the club in a tweet
#   "pings": a list of things to ping. Each entry has an "id" and a "type"
#            "type" is "role" (pings a role) or "user" (pings a person)
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


def load_posted_ids():
    if os.path.exists(POSTED_FILE):
        with open(POSTED_FILE, "r") as f:
            return [line.strip() for line in f if line.strip()]
    return []


def save_posted_ids(id_list):
    trimmed = id_list[-MAX_STORED_IDS:]
    with open(POSTED_FILE, "w") as f:
        f.write("\n".join(trimmed) + "\n")


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


def get_ping(text_lower):
    """Return ping string(s) for any clubs mentioned, roles or users."""
    pings = []
    for club_name, data in CLUB_ROLES.items():
        if any(kw in text_lower for kw in data["keywords"]):
            for p in data["pings"]:
                if p.get("type") == "user":
                    pings.append(f"<@{p['id']}>")       # user ping
                else:
                    pings.append(f"<@&{p['id']}>")      # role ping
            print(f"     -> Club match: {club_name}")
    return " ".join(pings)


def send_to_discord(link, content, image_url=None, ping=""):
    embed = {
        "description": content,
        "color": 0xDA020E if ping else 0x1DA1F2,  # red if a club ping, else blue
        "url": link,
        "author": {"name": "🚨 HERE WE GO! 🚨"},
        "footer": {"text": "Fabrizio Romano"}
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
        "allowed_mentions": {"parse": ["roles", "users"]}  # allow role + user pings
    }
    resp = requests.post(DISCORD_WEBHOOK, json=payload)
    print(f"Discord response: {resp.status_code}")


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
    print(f"\nLoaded {len(posted_ids)} previously posted IDs.")
    print(f"Found {len(items)} tweets in feed. Checking latest {TWEETS_TO_CHECK}.\n")
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
            image_url = extract_image(description, base_url)
            ping = get_ping(text_lower)
            print(f"     -> MATCH! Ping: {ping if ping else 'none'} | Image: {image_url}")
            print("     -> Sending to Discord...\n")
            send_to_discord(tweet_link, tweet_text, image_url, ping)
        else:
            print("     -> No keyword match.\n")

        posted_ids.append(tweet_id)
        posted_set.add(tweet_id)

    save_posted_ids(posted_ids)
    print("=" * 60)
    print(f"Done. Storing {min(len(posted_ids), MAX_STORED_IDS)} IDs.")

def normalize_id(raw_id, link):
    """Extract just the numeric tweet ID so it's the same across all
    Nitter instances (prevents duplicate posts)."""
    # Try to find status/<numbers> in the guid or link
    for source in (raw_id, link):
        if source:
            m = re.search(r'status/(\d+)', source)
            if m:
                return m.group(1)
    # Fallback: strip instance domain, keep the path
    return raw_id


if __name__ == "__main__":
    main()
