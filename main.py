import requests
import os
import re
import html
from datetime import datetime

# CONFIGURATION
DISCORD_WEBHOOK = os.environ.get('DISCORD_WEBHOOK')
USERNAME = "FabrizioRomano"
KEYWORD = "here we go"
POSTED_FILE = "posted_ids.txt"
TWEETS_TO_CHECK = 5

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
            return set(line.strip() for line in f if line.strip())
    return set()

def save_posted_id(tweet_id):
    with open(POSTED_FILE, "a") as f:
        f.write(tweet_id + "\n")

def send_to_discord(link, content):
    payload = {
        "content": f"🚨 **HERE WE GO!** 🚨\n\n{content}\n\n[Read on X]({link})",
        "username": "Fabrizio Tracker"
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

    items = re.findall(r'<item>(.*?)</item>', rss_data, re.DOTALL)
    if not items:
        print("No tweets found in feed.")
        return

    posted_ids = load_posted_ids()
    print(f"\nLoaded {len(posted_ids)} previously posted IDs.")
    print(f"Found {len(items)} tweets in feed. Checking latest {TWEETS_TO_CHECK}.\n")
    print("=" * 60)

    recent_items = items[:TWEETS_TO_CHECK]

    for i, item in enumerate(reversed(recent_items)):
        title_match = re.search(r'<title>(.*?)</title>', item, re.DOTALL)
        link_match = re.search(r'<link>(.*?)</link>', item, re.DOTALL)
        guid_match = re.search(r'<guid>(.*?)</guid>', item, re.DOTALL)

        if not (title_match and link_match and guid_match):
            print(f"[{i}] Could not parse fields, skipping.")
            continue

        # Decode HTML entities (&amp; &#39; etc.) so matching works
        tweet_text = html.unescape(title_match.group(1))
        tweet_link = link_match.group(1).strip()
        tweet_id = guid_match.group(1).strip()

        already_seen = tweet_id in posted_ids
        has_keyword = KEYWORD in tweet_text.lower()

        # DEBUG output for every tweet
        print(f"[{i}] TEXT: {tweet_text[:100]}")
        print(f"     ID: {tweet_id}")
        print(f"     Already seen? {already_seen} | Has keyword? {has_keyword}")

        if already_seen:
            print("     -> SKIP (already posted/seen)\n")
            continue

        if has_keyword:
            print("     -> MATCH! Sending to Discord...\n")
            send_to_discord(tweet_link, tweet_text)
        else:
            print("     -> No keyword match.\n")

        save_posted_id(tweet_id)
        posted_ids.add(tweet_id)

    print("=" * 60)
    print("Done.")

if __name__ == "__main__":
    main()
