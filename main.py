import requests
import os
import html
import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote

# CONFIGURATION
DISCORD_WEBHOOK = os.environ.get('DISCORD_WEBHOOK')
USERNAME = "FabrizioRomano"
KEYWORDS = ["here we go", "done deal", "medical booked", "medical scheduled"]   # add more phrases here anytime
POSTED_FILE = "posted_ids.txt"
TWEETS_TO_CHECK = 20
MAX_STORED_IDS = 100

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
    """Find the first image URL in the description and convert it to the
    original pbs.twimg.com URL (more reliable in Discord)."""
    if not description:
        return None

    match = re.search(r'<img[^>]+src="([^"]+)"', description)
    if not match:
        return None

    img_url = html.unescape(match.group(1))

    # Make relative URLs absolute first (e.g. /pic/... -> https://instance/pic/...)
    if img_url.startswith("/"):
        img_url = base_url.rstrip("/") + img_url

    # --- Convert Nitter proxy URL back to original Twitter URL ---
    # Nitter format: https://<instance>/pic/[orig/]media%2FFilename.jpg?params
    pic_match = re.search(r'/pic/(?:orig/)?(.+)$', img_url)
    if pic_match:
        encoded_path = pic_match.group(1)
        encoded_path = encoded_path.split("?")[0]      # strip query params
        decoded_path = unquote(encoded_path)           # %2F -> /
        twitter_url = "https://pbs.twimg.com/" + decoded_path
        print(f"     Converted Nitter img -> {twitter_url}")
        return twitter_url

    return img_url


def send_to_discord(link, content, image_url=None):
    embed = {
        "description": content,
        "color": 0x1DA1F2,  # Twitter blue
        "url": link,
        "author": {"name": "🚨 HERE WE GO! 🚨"},
        "footer": {"text": "Fabrizio Romano"}
    }
    if image_url:
        embed["image"] = {"url": image_url}

    payload = {
        "content": f"[Read on X]({link})",
        "username": "Fabrizio Tracker",
        "embeds": [embed]
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
        tweet_id = guid_el.text.strip() if guid_el is not None and guid_el.text else tweet_link
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
            print(f"     -> MATCH! Image: {image_url}")
            print("     -> Sending to Discord...\n")
            send_to_discord(tweet_link, tweet_text, image_url)
        else:
            print("     -> No keyword match.\n")

        posted_ids.append(tweet_id)
        posted_set.add(tweet_id)

    save_posted_ids(posted_ids)
    print("=" * 60)
    print(f"Done. Storing {min(len(posted_ids), MAX_STORED_IDS)} IDs.")


if __name__ == "__main__":
    main()
