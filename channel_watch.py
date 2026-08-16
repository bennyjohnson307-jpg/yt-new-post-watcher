#!/usr/bin/env python3
"""
Watches multiple YouTube channels' Community tabs, alerts on new posts,
and automatically keeps the engagement watcher's list in sync: when a
channel gets a new post, this removes that same channel's previous
post link (which it already knows precisely) and adds the new one -
so the list never grows unbounded, and other channels'/manually-added
links are never touched. Also tracks consecutive failures per channel
and sends one watchdog alert if a channel stops being readable.
"""

import json
import os
import re
import sys
import urllib.request

CHANNEL_URLS = [
    u.strip() for u in os.environ["CHANNEL_COMMUNITY_URLS"].split(",") if u.strip()
]
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
STATE_FILE = os.environ.get("STATE_FILE", "last_post.json")
GH_PAT = os.environ["GH_PAT"]
ENGAGEMENT_REPO = os.environ.get(
    "ENGAGEMENT_REPO", "bennyjohnson307-jpg/yt-community-watcher"
)
FAIL_ALERT_THRESHOLD = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="ignore")


def extract_yt_initial_data(html: str) -> dict:
    m = re.search(r"var ytInitialData\s*=\s*(\{.*?\});</script>", html, re.S)
    if not m:
        m = re.search(r'ytInitialData"\]\s*=\s*(\{.*?\});', html, re.S)
    if not m:
        raise RuntimeError("Could not find ytInitialData in the page.")
    return json.loads(m.group(1))


def text_of(node):
    if not node:
        return ""
    if "simpleText" in node:
        return node["simpleText"]
    return "".join(r.get("text", "") for r in node.get("runs", []))


def find_newest_post(data: dict):
    try:
        tabs = data["contents"]["twoColumnBrowseResultsRenderer"]["tabs"]
        for tab in tabs:
            content = tab.get("tabRenderer", {}).get("content", {})
            sections = content.get("sectionListRenderer", {}).get("contents", [])
            for section in sections:
                items = section.get("itemSectionRenderer", {}).get("contents", [])
                for item in items:
                    thread = item.get("backstagePostThreadRenderer")
                    if thread:
                        post = thread.get("post", {}).get("backstagePostRenderer")
                        if post and post.get("postId"):
                            return post["postId"], text_of(post.get("contentText"))[:200]
    except (KeyError, TypeError, IndexError):
        pass
    return None, None


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def notify(title: str, message: str, priority: str = "urgent"):
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority, "Tags": "bell,speaker"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=15)


def gh_api_request(url: str, method: str = "GET", body: dict = None):
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {GH_PAT}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def sync_engagement_watcher(new_post_id: str, old_post_id):
    """Removes this channel's known previous post link (if any) and
    adds the new one - keeping the watched list from growing forever,
    without touching other channels' or manually-added links."""
    var_url = f"https://api.github.com/repos/{ENGAGEMENT_REPO}/actions/variables/COMMUNITY_POST_URLS"
    try:
        current = gh_api_request(var_url)
        existing = [u.strip() for u in current.get("value", "").split(",") if u.strip()]
    except Exception as e:
        print(f"Could not read engagement watcher's current list: {e}")
        return False

    new_link = f"https://www.youtube.com/post/{new_post_id}"
    old_link = f"https://www.youtube.com/post/{old_post_id}" if old_post_id else None

    changed = False
    if old_link and old_link in existing:
        existing.remove(old_link)
        print(f"Removed this channel's previous post from watch list: {old_link}")
        changed = True

    if new_link not in existing:
        existing.append(new_link)
        changed = True

    if not changed:
        print("Watch list already correct, no update needed.")
        return True

    new_value = ",".join(existing)
    try:
        gh_api_request(
            var_url, method="PATCH",
            body={"name": "COMMUNITY_POST_URLS", "value": new_value},
        )
        print("Engagement watcher's list synced successfully.")
        return True
    except Exception as e:
        print(f"Could not update engagement watcher's list: {e}")
        return False


def check_channel(channel_url: str, state: dict):
    entry = state.get(channel_url, {})
    fail_count = entry.get("fail_count", 0) if isinstance(entry, dict) else 0
    last_seen = entry.get("last_post_id") if isinstance(entry, dict) else entry

    try:
        html = fetch_html(channel_url)
        data = extract_yt_initial_data(html)
        post_id, preview = find_newest_post(data)
    except Exception as e:
        fail_count += 1
        print(f"[{channel_url}] fetch/parse failed ({fail_count}x): {e}")
        state[channel_url] = {"last_post_id": last_seen, "fail_count": fail_count}
        if fail_count == FAIL_ALERT_THRESHOLD:
            notify(
                "Watcher problem",
                f"{channel_url}\nFailed {fail_count} times in a row - may need a look.",
                priority="default",
            )
        return

    if not post_id:
        fail_count += 1
        print(f"[{channel_url}] could not find post feed ({fail_count}x).")
        state[channel_url] = {"last_post_id": last_seen, "fail_count": fail_count}
        if fail_count == FAIL_ALERT_THRESHOLD:
            notify(
                "Watcher problem",
                f"{channel_url}\nCouldn't find the post feed {fail_count} times in a row.",
                priority="default",
            )
        return

    print(f"[{channel_url}] newest post id = {post_id}")

    if last_seen is None:
        print(f"[{channel_url}] first run - baseline recorded.")
    elif post_id != last_seen:
        link = f"https://www.youtube.com/post/{post_id}"
        notify(
            "New community post!",
            f"{channel_url}\n{link}\n{preview}" if preview else f"{channel_url}\n{link}",
        )
        print(f"[{channel_url}] new post detected - notification sent.")
        sync_engagement_watcher(post_id, last_seen)
    else:
        print(f"[{channel_url}] no new post since last check.")

    state[channel_url] = {"last_post_id": post_id, "fail_count": 0}


def main():
    state = load_state()
    for channel_url in CHANNEL_URLS:
        check_channel(channel_url, state)
    save_state(state)


if __name__ == "__main__":
    main()
