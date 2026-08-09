#!/usr/bin/env python3
"""
Watches multiple YouTube channels' Community tabs and alerts on new posts.
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


def notify(title: str, message: str):
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": "high", "Tags": "bell,speaker"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=15)


def check_channel(channel_url: str, state: dict):
    try:
        html = fetch_html(channel_url)
        data = extract_yt_initial_data(html)
    except Exception as e:
        print(f"[{channel_url}] fetch/parse failed: {e}")
        return

    post_id, preview = find_newest_post(data)
    if not post_id:
        print(f"[{channel_url}] could not find this channel's post feed.")
        return

    last_seen = state.get(channel_url)
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
    else:
        print(f"[{channel_url}] no new post since last check.")

    state[channel_url] = post_id


def main():
    state = load_state()
    for channel_url in CHANNEL_URLS:
        check_channel(channel_url, state)
    save_state(state)


if __name__ == "__main__":
    main()
