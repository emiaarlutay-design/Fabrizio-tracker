import requests
import os
import html
import xml.etree.ElementTree as ET

# CONFIGURATION
DISCORD_WEBHOOK = os.environ.get('DISCORD_WEBHOOK')
USERNAME = "FabrizioRomano"
KEYWORD = "here we go"
POSTED_FILE = "posted_ids.txt"
TWEETS_TO_CHECK = 20
MAX_STORED_IDS = 100   # keep only the last 100 IDs

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
    """Return list (ordered) of previously posted IDs."""
    if os.path.exists(POSTED_FILE):
        with open(POSTED_FILE, "r") as f:
            return [line.strip() for line in f if line.strip()]
    return []

def save_posted_ids(id_list):
    """Rewrite the file, keeping only the most recent MAX_STORED_IDS."""
    trimmed = id_list[-MAX_STORED_IDS:]
    with open(POSTED_FILE, "w") as f:
        f.write("\n".join(trimmed) + "\n")

def send_to_discord(link, content):
    payload = {
        "content": f"🚨 **HERE WE GO!** 🚨\n\n{content}\n\n[Read on X]({link})",
        "username": "Lutay FootBot"
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
                return response.text
            else:
                print(f"❌ {base} returned status {response.status_code} or no items")
        except Exception as e:
            print(f"❌ {base} failed: {e}")
    return None

def main():
    rss_data = fetch_rss()
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
    posted_set = set(posted_ids)   # fast lookups
    print(f"\nLoaded {len(posted_ids)} previously posted IDs.")
    print(f"Found {len(items)} tweets in feed. Checking latest {TWEETS_TO_CHECK}.\n")
    print("=" * 60)

    recent_items = items[:TWEETS_TO_CHECK]

    for i, item in enumerate(reversed(recent_items)):
        title_el = item.find("title")
        link_el = item.find("link")
        guid_el = item.find("guid")

        tweet_text = html.unescape(title_el.text) if title_el is not None and title_el.text else ""
        tweet_link = link_el.text.strip() if link_el is not None and link_el.text else ""
        tweet_id = guid_el.text.strip() if guid_el is not None and guid_el.text else tweet_link

        if not tweet_text or not tweet_id:
            print(f"[{i}] Empty fields, skipping.")
            continue

        already_seen = tweet_id in posted_set
        has_keyword = KEYWORD in tweet_text.lower()

        print(f"[{i}] TEXT: {tweet_text[:100]}")
        print(f"     Already seen? {already_seen} | Has keyword? {has_keyword}")

        if already_seen:
            print("     -> SKIP (already posted)\n")
            continue

        if has_keyword:
            print("     -> MATCH! Sending to Discord...\n")
            send_to_discord(tweet_link, tweet_text)
        else:
            print("     -> No keyword match.\n")

        # Mark as seen either way
        posted_ids.append(tweet_id)
        posted_set.add(tweet_id)

    # Save once at the end, trimmed to last MAX_STORED_IDS
    save_posted_ids(posted_ids)
    print("=" * 60)
    print(f"Done. Storing {min(len(posted_ids), MAX_STORED_IDS)} IDs.")

if __name__ == "__main__":
    main()
