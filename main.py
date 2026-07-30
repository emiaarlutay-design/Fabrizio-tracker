import requests
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
import time

# CONFIGURATION
DISCORD_WEBHOOK = os.environ.get('DISCORD_WEBHOOK')
# Using a public Nitter instance to scrape Fabrizio Romano without API keys
# Nitter instances can change; if one fails, try another like nitter.net or nitter.privacydev.net
NITTER_URL = "https://nitter.privacydev.net/FabrizioRomano/rss" 
LAST_TWEET_FILE = "last_tweet.txt"

def get_last_known_id():
    if os.path.exists(LAST_TWEET_FILE):
        with open(LAST_TWEET_FILE, "r") as f:
            return f.read().strip()
    return None

def save_last_known_id(tweet_id):
    with open(LAST_TWEET_FILE, "w") as f:
        f.write(tweet_id)

def send_to_discord(title, link, content):
    payload = {
        "content": f"🚨 **HERE WE GO!** 🚨\n\n{content}\n\n[Read on X]({link})",
        "username": "Lutay FootBot",
        "avatar_url": "https://imgur.com/a/DslvNrQ.png" # Optional avatar
    }
    requests.post(DISCORD_WEBHOOK, json=payload)

def main():
    try:
        response = requests.get(NITTER_URL, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
        
        # Parse simple RSS XML manually to avoid heavy dependencies
        items = re.findall(r'<item>(.*?)</item>', response.text, re.DOTALL)
        
        if not items:
            print("No tweets found or Nitter down.")
            return

        # Get the newest tweet (first item in RSS)
        latest_item = items[0]
        
        # Extract Title (usually the tweet text)
        title_match = re.search(r'<title>(.*?)</title>', latest_item)
        link_match = re.search(r'<link>(.*?)</link>', latest_item)
        guid_match = re.search(r'<guid>(.*?)</guid>', latest_item) # Unique ID
        
        if not all([title_match, link_match, guid_match]):
            return

        tweet_text = title_match.group(1)
        tweet_link = link_match.group(1)
        tweet_id = guid_match.group(1)

        last_known = get_last_known_id()

        # If we haven't seen this tweet AND it contains "Here we go"
        if last_known != tweet_id:
            if "here we go" in tweet_text.lower():
                print(f"Match found! Sending to Discord: {tweet_text}")
                send_to_discord("Transfer Alert", tweet_link, tweet_text)
            else:
                print(f"New tweet found but no match: {tweet_text[:50]}...")
            
            # Update state regardless of match to prevent re-checking old tweets
            save_last_known_id(tweet_id)
        else:
            print("No new tweets since last check.")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()



# ... inside main(), after extracting latest_item ...
# Extract PubDate
date_match = re.search(r'<pubDate>(.*?)</pubDate>', latest_item)
if date_match:
    pub_date_str = date_match.group(1)
    tweet_date = parsedate_to_datetime(pub_date_str)
    now = datetime.now(tweet_date.tzinfo)
    
    # Only process if tweet is less than 15 minutes old (covers the cron interval)
    if (now - tweet_date).total_seconds() < 900: 
        if "here we go" in tweet_text.lower():
             send_to_discord("Transfer Alert", tweet_link, tweet_text)
             print("Alert sent!")
        else:
             print("Recent tweet, but no keyword.")
    else:
        print("Tweet too old, skipping.")
else:
    print("Could not parse date.")
