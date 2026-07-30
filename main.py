import requests
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime

# CONFIGURATION
DISCORD_WEBHOOK = os.environ.get('DISCORD_WEBHOOK')
USERNAME = "FabrizioRomano"
KEYWORD = "here we go"

# List of Nitter instances to try (fallback system)
# If all these die, check https://status.d420.de/ for new working ones
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.poast.org",
    "https://nitter.tiekoetter.com",
    "https://lightbrd.com",
    "https://nitter.privacyredirect.com",
    "https://xcancel.com"
]

def send_to_discord(link, content):
    payload = {
        "content": f"🚨 **HERE WE GO!** 🚨\n\n{content}\n\n[Read on X]({link})",
        "username": "Lutay FootBot"
    }
    resp = requests.post(DISCORD_WEBHOOK, json=payload)
    print(f"Discord response: {resp.status_code}")

def fetch_rss():
    """Try each instance until one returns valid data."""
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

    # Newest tweet is the first item
    latest_item = items[0]

    title_match = re.search(r'<title>(.*?)</title>', latest_item)
    link_match = re.search(r'<link>(.*?)</link>', latest_item)
    date_match = re.search(r'<pubDate>(.*?)</pubDate>', latest_item)

    if not (title_match and link_match and date_match):
        print("Could not parse tweet fields.")
        return

    tweet_text = title_match.group(1)
    tweet_link = link_match.group(1)
    pub_date_str = date_match.group(1)

    # Check how old the tweet is
    tweet_date = parsedate_to_datetime(pub_date_str)
    now = datetime.now(tweet_date.tzinfo)
    age_seconds = (now - tweet_date).total_seconds()

    print(f"Latest tweet ({int(age_seconds)}s old): {tweet_text[:80]}")

    # Only post if tweet is recent (within 15 mins) AND contains keyword
    if age_seconds < 900
        if KEYWORD in tweet_text.lower():
            print("MATCH! Sending to Discord...")
            send_to_discord(tweet_link, tweet_text)
        else:
            print("Recent tweet, but no keyword match.")
    else:
        print("Tweet too old, skipping.")

if __name__ == "__main__":
    main()
