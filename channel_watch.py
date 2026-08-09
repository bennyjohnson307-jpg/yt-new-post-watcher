#!/usr/bin/env python3
"""
Watches a YouTube channel's Community tab and alerts on a new post.
"""

import json
import os
import re
import sys
import urllib.request

CHANNEL_COMMUNITY_URL = os.environ["CHANNEL_COMMUNITY_URL"]
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


def find_newest_post(data: dict):
    """Returns (post_id, preview_text) of the first community post found."""
    result = [None, None]

    def text_of(node):
        if not node:
            return ""
        if "simpleText" in node:
            return node["simpleText"]
        return "".join(r.get("text", "") for r in node.get("runs", []))

    def walk(obj):
        if result[0]:
            return
        if isinstance(obj, dict):
            post = obj.get("backstagePostRenderer")
            if post and post.get("postId"):
                result[0] = post["postId"]
                result[1] = text_of(post.get("contentText"))[:200]
                return
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(data)
    return result[0], result[1]


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


def main():
    html = fetch_html(CHANNEL_COMMUNITY_URL)
    data = extract_yt_initial_data(html)
    post_id, preview = find_newest_post(data)

    if not post_id:
        print("No community post found on the page.")
        sys.exit(0)

    state = load_state()
    last_seen = state.get("last_post_id")

    print(f"newest post id = {post_id}")

    if last_seen is None:
        print("First run - baseline recorded.")
    elif post_id != last_seen:
        link = f"https://www.youtube.com/post/{post_id}"
        notify(
            "New community post!",
            f"{link}\n{preview}" if preview else link,
        )
        print("New post detected - notification sent.")
    else:
        print("No new post since last check.")

    state["last_post_id"] = post_id
    save_state(state)


if __name__ == "__main__":
    main()
