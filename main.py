import requests
import os
import html
import re
import time
import json
import xml.etree.ElementTree as ET
from urllib.parse import unquote, quote
from datetime import datetime, timezone

# ============================================================
# LOAD CONFIG (#32)
# ============================================================
with open("config.json", "r", encoding="utf-8") as f:
    CFG = json.load(f)

KEYWORDS = [k.lower() for k in CFG["keywords"]]
BIG_MONEY_THRESHOLD = CFG.get("big_money_threshold", 40)
MILESTONE_EVERY = CFG.get("milestone_every", 50)
CLUB_ROLES = CFG["clubs"]
NITTER_INSTANCES = CFG["nitter_instances"]

# ============================================================
# SECRETS / ENV
# ============================================================
DISCORD_WEBHOOK = os.environ.get('DISCORD_WEBHOOK')
HEALTH_WEBHOOK = os.environ.get('HEALTH_WEBHOOK', DISCORD_WEBHOOK)
BOT_TOKEN = os.environ.get('BOT_TOKEN')  # for reactions (#21, #29)

USERNAME = "FabrizioRomano"
POSTED_FILE = "posted_ids.txt"
HEALTH_FILE = "health_state.txt"
STATS_FILE = "stats.json"
TWEETS_TO_CHECK = 20

# --- Safety / behavior ---
DRY_RUN = False
MAX_POSTS_PER_RUN = 5
POST_DELAY_SECONDS = 1
PROXIMITY_LIMIT = 100
ALERT_AFTER_FAILURES = 5
TRIM_THRESHOLD = 100
TRIM_KEEP = 80

FABRIZIO_AVATAR = "https://pbs.twimg.com/profile_images/874276197357596672/kUuht00m_400x400.jpg"


# ============================================================
# STATS (#22 milestones, #26 instance ranking)
# ============================================================
def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"total_posted": 0, "instance_success": {}}


def save_stats(stats):
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f, indent=2)


# ============================================================
# POSTED IDS
# ============================================================
def load_posted_ids():
    if os.path.exists(POSTED_FILE):
        with open(POSTED_FILE, "r") as f:
            return [line.strip() for line in f if line.strip()]
    return []


def save_posted_ids(id_list):
    if len(id_list) > TRIM_THRESHOLD:
        id_list = id_list[-TRIM_KEEP:]
        print(f"Trimmed posted_ids to last {TRIM_KEEP}.")
    with open(POSTED_FILE, "w") as f:
        f.write("\n".join(id_list) + "\n")


def normalize_id(raw_id, link):
    for source in (raw_id, link):
        if source:
            m = re.search(r'status/(\d+)', source)
            if m:
                return m.group(1)
    return raw_id


# ============================================================
# HEALTH CHECK (#9)
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
        print(f"Health alert failed: {e}")


# ============================================================
# MEDIA EXTRACTION (#15 video, images)
# ============================================================
def extract_media(description, base_url):
    image_url = None
    has_video = False
    if not description:
        return image_url, has_video

    if re.search(r'<video', description) or re.search(r'\.mp4', description) \
            or "/video/" in description or "tw_video" in description:
        has_video = True

    img_match = re.search(r'<img[^>]+src="([^"]+)"', description)
    src = None
    if img_match:
        src = img_match.group(1)
    else:
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
            img_url = "https://pbs.twimg.com/" + unquote(encoded_path)
        image_url = img_url

    return image_url, has_video


# ============================================================
# #17 LOAN vs PERMANENT
# ============================================================
def detect_deal_type(text_lower):
    if "loan with option" in text_lower or "loan with an option" in text_lower \
            or "loan with obligation" in text_lower:
        return "🔁 Loan (with option)"
    if "on loan" in text_lower or "loan deal" in text_lower or "loan move" in text_lower:
        return "🔁 Loan"
    if "free transfer" in text_lower or "free agent" in text_lower:
        return "🆓 Free Transfer"
    if "permanent" in text_lower:
        return "✅ Permanent"
    return None


# ============================================================
# #3 MONEY DETECTION
# ============================================================
def detect_money(text):
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
# CLUB MATCHING (proximity) + emoji for reactions
# ============================================================
def get_club_matches(text_lower):
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
                cs, ce = idx, idx + len(kw)
                for (ks, ke) in keyword_spans:
                    if max(cs, ks) - min(ce, ke) <= PROXIMITY_LIMIT:
                        matched = True
                        break
                start = idx + 1
            if matched:
                break
        if matched:
            matches.append({"name": club_name, **data})
            print(f"     -> Club match: {club_name}")
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
# DISCORD POSTING (returns message id + channel id for reactions)
# ============================================================
def send_to_discord(link, content, image_url=None, ping="",
                    crest=None, money_display=None, is_big_money=False,
                    has_video=False, deal_type=None):
    if DRY_RUN:
        print(f"     [DRY RUN] ping={ping or 'none'} money={money_display} "
              f"deal={deal_type} video={has_video} image={image_url}")
        return None, None

    color = 0xFFD700 if is_big_money else (0xDA020E if ping else 0x1DA1F2)
    author_name = f"💰 BIG MONEY — {money_display} 💰" if is_big_money else "🚨 HERE WE GO! 🚨"

    embed = {
        "description": content,
        "color": color,
        "url": link,
        "author": {"name": author_name, "icon_url": FABRIZIO_AVATAR},
        "footer": {"text": "Fabrizio Romano", "icon_url": FABRIZIO_AVATAR},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    if crest:
        embed["thumbnail"] = {"url": crest}

    fields = []
    if money_display:
        fields.append({"name": "💵 Fee", "value": money_display, "inline": True})
    if deal_type:
        fields.append({"name": "📋 Type", "value": deal_type, "inline": True})
    if has_video:
        fields.append({"name": "🎥 Media", "value": f"[Video/GIF on X]({link})", "inline": True})
    if fields:
        embed["fields"] = fields

    # Use Fabrizio's real tweet photo
    if image_url:
        embed["image"] = {"url": image_url}

    msg_content = f"{ping} 🔥 [Read on X]({link})" if ping else f"[Read on X]({link})"

    payload = {
        "content": msg_content,
        "username": "Lutay FootBot",
        "embeds": [embed],
        "allowed_mentions": {"parse": ["roles", "users"]}
    }

    # Add ?wait=true so Discord returns the message object (id + channel_id)
    url = DISCORD_WEBHOOK + ("&wait=true" if "?" in DISCORD_WEBHOOK else "?wait=true")

    try:
        resp = requests.post(url, json=payload, timeout=15)
        print(f"     Discord response: {resp.status_code}")

        if resp.status_code == 429:
            retry = resp.json().get("retry_after", 2)
            print(f"     Rate limited, waiting {retry}s...")
            time.sleep(float(retry) + 0.5)
            resp = requests.post(url, json=payload, timeout=15)
            print(f"     Retry: {resp.status_code}")

        if resp.status_code in (200, 204):
            try:
                data = resp.json()
                return data.get("id"), data.get("channel_id")
            except Exception:
                return "ok", None
        return None, None
    except Exception as e:
        print(f"     Discord error: {e}")
        return None, None


# ============================================================
# #21 + #29 REACTIONS (needs BOT_TOKEN)
# ============================================================
def add_reactions(channel_id, message_id, emojis):
    if not BOT_TOKEN or not channel_id or not message_id or message_id == "ok":
        if not BOT_TOKEN:
            print("     (No BOT_TOKEN, skipping reactions)")
        return
    headers = {"Authorization": f"Bot {BOT_TOKEN}"}
    for emoji in emojis:
        try:
            enc = quote(emoji, safe="")  # handles unicode + custom name:id
            api = (f"https://discord.com/api/v10/channels/{channel_id}"
                   f"/messages/{message_id}/reactions/{enc}/@me")
            r = requests.put(api, headers=headers, timeout=15)
            if r.status_code in (200, 204):
                print(f"     Reaction added: {emoji}")
            else:
                print(f"     Reaction {emoji} failed: {r.status_code} {r.text[:80]}")
            time.sleep(0.35)  # avoid reaction rate limit
        except Exception as e:
            print(f"     Reaction error {emoji}: {e}")


# ============================================================
# #22 MILESTONE
# ============================================================
def send_milestone(total):
    payload = {
        "content": f"🎉 **MILESTONE!** 🎉\nThe bot has now tracked **{total}** confirmed transfers! 🚨⚽",
        "username": "Lutay FootBot"
    }
    try:
        requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
        print(f"Milestone {total} posted.")
    except Exception as e:
        print(f"Milestone failed: {e}")


# ============================================================
# RSS FETCH (#26 ranked instances)
# ============================================================
def fetch_rss(stats):
    headers = {'User-Agent': 'Mozilla/5.0'}
    success_map = stats.get("instance_success", {})
    ordered = sorted(NITTER_INSTANCES,
                     key=lambda u: success_map.get(u, 0), reverse=True)
    for base in ordered:
        url = f"{base}/{USERNAME}/rss"
        try:
            print(f"Trying: {url}")
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200 and "<item>" in response.text:
                print(f"✅ Success with {base}")
                success_map[base] = success_map.get(base, 0) + 1
                stats["instance_success"] = success_map
                return response.text, base
            else:
                print(f"❌ {base} status {response.status_code}")
        except Exception as e:
            print(f"❌ {base} failed: {e}")
    stats["instance_success"] = success_map
    return None, None


# ============================================================
# MAIN
# ============================================================
def main():
    stats = load_stats()
    rss_data, base_url = fetch_rss(stats)

    failures = load_failure_count()
    if not rss_data:
        failures += 1
        save_failure_count(failures)
        save_stats(stats)
        print(f"All instances failed. Consecutive: {failures}")
        if failures == ALERT_AFTER_FAILURES:
            send_health_alert(f"All Nitter instances failed **{failures}x in a row**. "
                              f"Instance list may need updating.")
        return
    else:
        if failures >= ALERT_AFTER_FAILURES:
            send_health_alert("✅ Recovered! Nitter is reachable again.")
        if failures != 0:
            save_failure_count(0)

    try:
        root = ET.fromstring(rss_data)
    except ET.ParseError as e:
        print(f"XML parse error: {e}")
        save_stats(stats)
        return

    items = root.findall(".//item")
    if not items:
        print("No items found.")
        save_stats(stats)
        return

    posted_ids = load_posted_ids()
    posted_set = set(posted_ids)
    posts_this_run = 0

    print(f"\nLoaded {len(posted_ids)} IDs. Total tracked ever: {stats.get('total_posted', 0)}")
    print(f"Found {len(items)} tweets. Checking latest {TWEETS_TO_CHECK}.")
    if DRY_RUN:
        print("⚠️  DRY_RUN ON — nothing will post.")
    print("=" * 60)

    for i, item in enumerate(reversed(items[:TWEETS_TO_CHECK])):
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
            continue

        already_seen = tweet_id in posted_set
        text_lower = tweet_text.lower()
        has_keyword = any(kw in text_lower for kw in KEYWORDS)

        print(f"[{i}] {tweet_text[:90]}")
        print(f"     Seen? {already_seen} | Keyword? {has_keyword}")

        if already_seen:
            print("     -> SKIP\n")
            continue

        if has_keyword:
            if posts_this_run >= MAX_POSTS_PER_RUN:
                print("     -> MAX_POSTS reached, marking seen.\n")
                posted_ids.append(tweet_id); posted_set.add(tweet_id)
                continue

            image_url, has_video = extract_media(description, base_url)
            matches = get_club_matches(text_lower)
            ping = build_ping_string(matches)
            crest = matches[0]["crest"] if matches else None
            money_display, money_value = detect_money(tweet_text)
            is_big = money_value >= BIG_MONEY_THRESHOLD
            deal_type = detect_deal_type(text_lower)

            print(f"     -> POSTING | ping={ping or 'none'} money={money_display} "
                  f"big={is_big} deal={deal_type} video={has_video} image={image_url}")

            msg_id, chan_id = send_to_discord(
                tweet_link, tweet_text, image_url, ping,
                crest=crest, money_display=money_display, is_big_money=is_big,
                has_video=has_video, deal_type=deal_type
            )

            success = (msg_id is not None) or DRY_RUN

            if success:
                # Reactions: club emojis (#29) + poll on big money (#21)
                emojis = []
                for m in matches:
                    if m.get("emoji"):
                        emojis.append(m["emoji"])
                if is_big:
                    emojis += ["👍", "👎"]
                if emojis and not DRY_RUN:
                    add_reactions(chan_id, msg_id, emojis)

                posts_this_run += 1
                posted_ids.append(tweet_id); posted_set.add(tweet_id)

                # #22 milestone
                stats["total_posted"] = stats.get("total_posted", 0) + 1
                if stats["total_posted"] % MILESTONE_EVERY == 0 and not DRY_RUN:
                    send_milestone(stats["total_posted"])

                print(f"     -> OK ({posts_this_run}/{MAX_POSTS_PER_RUN})\n")
                time.sleep(POST_DELAY_SECONDS)
            else:
                print("     -> Discord FAILED, retry next run.\n")
        else:
            print("     -> No match.\n")
            posted_ids.append(tweet_id); posted_set.add(tweet_id)

    save_posted_ids(posted_ids)
    save_stats(stats)
    print("=" * 60)
    print(f"Done. Posted {posts_this_run}. Total ever: {stats.get('total_posted', 0)}")


if __name__ == "__main__":
    main()
