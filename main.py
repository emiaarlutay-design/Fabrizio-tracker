
import requests
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime

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
    "https://xcancel.com"
]

def load_posted_ids():
    """Load the set of tweet IDs we've already sent to Discord."""
    if os.path.exists(POSTED_FILE):
        with open(POSTED_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_posted_id(tweet_id):
    """Append a newly posted tweet ID to the file."""
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
    print(f"Loaded {len(posted_ids)} previously posted IDs.")

    # Check the latest N tweets (process oldest->newest so Discord order is chronological)
    recent_items = items[:TWEETS_TO_CHECK]

    for item in reversed(recent_items):
        title_match = re.search(r'<title>(.*?)</title>', item)
        link_match = re.search(r'<link>(.*?)</link>', item)
        guid_match = re.search(r'<guid>(.*?)</guid>', item)

        if not (title_match and link_match and guid_match):
            continue

        tweet_text = title_match.group(1)
        tweet_link = link_match.group(1)
        tweet_id = guid_match.group(1)

        # Skip if we've already posted this one
        if tweet_id in posted_ids:
            continue

        # Check for keyword
        if KEYWORD in tweet_text.lower():
            print(f"MATCH! Sending: {tweet_text[:80]}")
            send_to_discord(tweet_link, tweet_text)
            save_posted_id(tweet_id)
            posted_ids.add(tweet_id)
        else:
            # Mark non-matching tweets as "seen" too, so we don't re-check them forever
            save_posted_id(tweet_id)
            posted_ids.add(tweet_id)
            print(f"Seen (no match): {tweet_text[:60]}")

    print("Done.")

if __name__ == "__main__":
    main()
