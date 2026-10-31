#!/usr/bin/env python3
"""Archive quote posts from the official @一言App Weibo account."""

import argparse
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UID = "5581983697"
API = "https://m.weibo.cn/api/container/getIndex"
BEIJING = dt.timezone(dt.timedelta(hours=8))
ATTRIBUTION_RE = re.compile(r"^[｜|]\s*(.+?)(?:《(.+?)》)?\s*$")
TAG_RE = re.compile(r"#.*?#")
HTML_RE = re.compile(r"<[^>]+>")


def clean_text(value):
    value = html.unescape(HTML_RE.sub("", value or ""))
    value = value.replace("\u200b", "").replace("\ufeff", "")
    lines = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def parse_created_at(value):
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        return parsed.astimezone(BEIJING)
    except (TypeError, ValueError):
        return dt.datetime.now(BEIJING)


def quote_from_mblog(mblog):
    raw = clean_text(mblog.get("text_raw") or mblog.get("text"))
    if not raw or mblog.get("isTop"):
        return None
    lines = raw.splitlines()
    attr_index = -1
    attr_match = None
    for index in range(len(lines) - 1, -1, -1):
        match = ATTRIBUTION_RE.match(lines[index])
        if match:
            attr_index, attr_match = index, match
            break
    if attr_index < 1:
        return None

    body_lines = lines[:attr_index]
    body = "\n".join(line for line in body_lines if not TAG_RE.fullmatch(line)).strip()
    if not body or len(body) < 8 or len(body) > 360:
        return None
    if any(word in body for word in ("抽奖", "转发微博", "购买链接", "一言历")):
        return None

    author = attr_match.group(1).strip(" —-·")
    work = (attr_match.group(2) or "").strip()
    created = parse_created_at(mblog.get("created_at"))
    bid = str(mblog.get("bid") or mblog.get("id") or "")
    key = bid or hashlib.sha256((body + author + work).encode("utf-8")).hexdigest()[:16]
    return {
        "id": key,
        "text": body,
        "from_who": author,
        "from": "《" + work + "》" if work else "",
        "date": created.date().isoformat(),
        "published_at": created.isoformat(),
        "source_account": "一言App",
        "source_url": "https://weibo.com/%s/%s" % (UID, bid) if bid else "https://weibo.com/u/" + UID,
    }


def fetch_page(page, cookie):
    params = urllib.parse.urlencode({
        "type": "uid",
        "value": UID,
        "containerid": "107603" + UID,
        "page": page,
    })
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://m.weibo.cn/u/" + UID,
        "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36",
    }
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(API + "?" + params, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)
    if not payload.get("ok"):
        raise RuntimeError("Weibo returned ok=%r on page %s" % (payload.get("ok"), page))
    return payload.get("data", {}).get("cards", [])


def load_archive(path):
    if not path.exists():
        return {"source": "一言App", "uid": UID, "quotes": []}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data.get("quotes"), list):
        raise ValueError("archive quotes must be a list")
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", default="quotes/yiyanapp.json")
    parser.add_argument("--pages", type=int, default=20)
    args = parser.parse_args()

    archive_path = Path(args.archive)
    archive = load_archive(archive_path)
    known = {str(item.get("id")) for item in archive["quotes"]}
    added = []
    cookie = os.environ.get("WEIBO_COOKIE", "").strip()

    for page in range(1, max(1, args.pages) + 1):
        try:
            cards = fetch_page(page, cookie)
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as error:
            if page == 1:
                raise RuntimeError("cannot fetch @一言App: %s" % error) from error
            print("Stopped after page %s: %s" % (page - 1, error), file=sys.stderr)
            break
        found = 0
        for card in cards:
            mblog = card.get("mblog") if isinstance(card, dict) else None
            if not mblog:
                continue
            quote = quote_from_mblog(mblog)
            if not quote:
                continue
            found += 1
            if quote["id"] not in known:
                known.add(quote["id"])
                added.append(quote)
        if not cards or (page > 1 and found == 0):
            break
        time.sleep(1.2)

    if not added:
        print("No new quote posts; archive remains at %s." % len(archive["quotes"]))
        return

    archive["quotes"].extend(added)
    archive["quotes"].sort(
        key=lambda item: (item.get("published_at", item.get("date", "")), item.get("id", "")),
        reverse=True,
    )
    archive["updated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    archive["source"] = "一言App"
    archive["uid"] = UID
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with archive_path.open("w", encoding="utf-8") as handle:
        json.dump(archive, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print("Added %s quotes; archive now has %s." % (len(added), len(archive["quotes"])))


if __name__ == "__main__":
    main()
