#!/usr/bin/env python3
"""Builds site/data.json for the stats page: profiles, follower counts and per-post numbers.

Runs in the GitHub Action every 6 hours (and locally for testing):

    BUFFER_API_KEY=... [IG_TOKEN=...] python3 collect.py

- Buffer API: published posts, their links and metrics.
- Instagram followers: Graph API through the linked Facebook Page (IG_TOKEN, Page token).
- TikTok followers: the public profile page (TikTok's own API needs an app review).
A source that fails keeps its last value from the live data.json, marked stale.
"""
import json, os, re, shutil, urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SITE = HERE / "site"
LIVE = "https://thesouldevotional.github.io/stats/data.json"
ORG = "6ae315866ceae7d1bd911b98"
IG_USER_ID = "17841442516994255"  # thesouldevotional, via the Facebook Page "Thesouldevotional"
CHANNELS = {"instagram": "6ae3213d2f1f5ed6480cb106", "tiktok": "6ae321e72f1f5ed6480cb5d9"}
PROFILES = {"instagram": "https://www.instagram.com/thesouldevotional/",
            "tiktok": "https://www.tiktok.com/@thesouldevotional"}
UA = "thesouldevotional-stats/1.0 (+https://thesouldevotional.github.io/stats/)"


def get(url, headers=None, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def buffer(query, variables=None):
    out = json.loads(get("https://api.buffer.com",
                         {"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["BUFFER_API_KEY"]},
                         json.dumps({"query": query, "variables": variables or {}}).encode()))
    if out.get("errors"):
        raise RuntimeError(out["errors"])
    return out["data"]


def buffer_posts():
    q = """query P($after: String) { posts(first: 50, after: $after, input: {organizationId: "%s",
      filter: {status: [sent, scheduled]}}) {
      edges { node { id status channelService dueAt sentAt externalLink text
        metrics { type name value } metricsUpdatedAt } }
      pageInfo { hasNextPage endCursor } } }""" % ORG
    posts, after = [], None
    while True:
        d = buffer(q, {"after": after})["posts"]
        posts += [e["node"] for e in d["edges"]]
        if not d["pageInfo"]["hasNextPage"]:
            return posts
        after = d["pageInfo"]["endCursor"]


def instagram_followers():
    # IG_TOKEN is the token of the Facebook Page linked to the account: it never expires, but Meta stops
    # its data access ~90 days after Ivana last authorized the app, so that date is reported too
    token = os.environ.get("IG_TOKEN")
    if not token:
        return None, None
    g = "https://graph.facebook.com/v21.0/"
    me = json.loads(get(f"{g}{IG_USER_ID}?fields=followers_count&access_token={token}"))
    try:
        until = json.loads(get(f"{g}debug_token?input_token={token}&access_token={token}"))["data"]["data_access_expires_at"]
        left = until - datetime.now(timezone.utc).timestamp() if until else None
    except Exception:
        left = None
    return me["followers_count"], left


def instagram_stories():
    """Live Instagram stories with their numbers (needs instagram_manage_insights): [(time, metrics)].
    Instagram keeps story insights only while the story is live (24h), and refuses them under 5 viewers."""
    token = os.environ.get("IG_TOKEN")
    if not token:
        return []
    g = "https://graph.facebook.com/v21.0/"
    out = []
    for s in json.loads(get(f"{g}{IG_USER_ID}/stories?fields=id,timestamp&access_token={token}")).get("data", []):
        m = {}
        try:
            for x in json.loads(get(f"{g}{s['id']}/insights?metric=views,reach,replies,shares,follows,profile_visits"
                                    f"&access_token={token}"))["data"]:
                v = (x.get("values") or [{}])[0].get("value", (x.get("total_value") or {}).get("value"))
                if v is not None:
                    m[x["name"]] = v
            nav = json.loads(get(f"{g}{s['id']}/insights?metric=navigation&breakdown=story_navigation_action_type"
                                 f"&access_token={token}"))["data"][0]
            for r in nav["total_value"]["breakdowns"][0]["results"]:
                m[r["dimension_values"][0]] = r["value"]  # tap_forward, tap_back, tap_exit, swipe_forward
        except Exception:
            pass
        out.append((datetime.strptime(s["timestamp"], "%Y-%m-%dT%H:%M:%S%z"), m))
    return out


def tiktok_followers():
    m = re.search(r'"followerCount":(\d+)', get(PROFILES["tiktok"]))
    return int(m.group(1)) if m else None


def main():
    try:
        last = json.loads(get(LIVE))
    except Exception:
        last = {}
    now = datetime.now(timezone.utc).isoformat(timespec="minutes")
    mapping = json.loads((HERE / "posts.json").read_text())  # buffer post id -> verse
    by_id = {pid: m for m in mapping for pid in m["buffer"].values()}
    stories = {m["buffer"].get("instagram_stories") for m in mapping}  # shown as their own row, not counted as posts

    followers, notes = {}, []
    for name, fn in [("instagram", instagram_followers), ("tiktok", tiktok_followers)]:
        prev = (last.get("followers") or {}).get(name) or {}
        try:
            res = fn()
            count, expires = res if isinstance(res, tuple) else (res, None)
        except Exception as e:
            count, expires = None, None
            notes.append(f"{name} followers: {type(e).__name__}")
        if count is None:
            followers[name] = {**prev, "stale": True} if prev else None
        else:
            followers[name] = {"count": count, "at": now, "stale": False}
            if expires:
                followers[name]["token_days"] = round(expires / 86400)

    # a story's numbers can only be read while it is live (24h), so keep the last ones we got
    kept = {x["id"]: x for x in last.get("posts") or [] if x.get("id") in stories and x.get("metrics")}
    try:
        live_stories = instagram_stories()
    except Exception as e:
        live_stories = []
        notes.append(f"instagram stories: {type(e).__name__}")
    posts = []
    for p in buffer_posts():
        m = by_id.get(p["id"], {})
        metrics = {x["type"]: x["value"] for x in p.get("metrics") or []}
        if "likes" not in metrics and "reactions" in metrics:  # TikTok reports likes as reactions
            metrics["likes"] = metrics.pop("reactions")
        if p["id"] in stories:
            if "comments" in metrics:  # Buffer calls story replies comments
                metrics["replies"] = metrics.pop("comments")
            if p["sentAt"]:  # add Instagram's own numbers for the stories that went up with this post
                sent = datetime.fromisoformat(p["sentAt"].replace("Z", "+00:00"))
                ig = {}
                for at, sm in live_stories:
                    if abs((at - sent).total_seconds()) < 3600:
                        for k, v in sm.items():  # reach: people, so the largest; the rest add up across the stories
                            ig[k] = max(ig.get(k, 0), v) if k == "reach" else ig.get(k, 0) + v
                metrics.update(ig)  # Instagram's numbers win over Buffer's for the same metric
        if p["id"] in kept:  # story numbers only grow: a metric missing now, or lower, keeps its last value
            merged = dict(kept[p["id"]]["metrics"])
            for k, v in metrics.items():
                merged[k] = max(v, merged.get(k, v))
            metrics = merged
        posts.append({
            "id": p["id"],
            "platform": "instagram stories" if p["id"] in stories else p["channelService"], "status": p["status"],
            "date": p["sentAt"] or p["dueAt"], "link": p["externalLink"],
            "verse": m.get("verse"), "verse_link": m.get("verse_link"), "palette": m.get("palette"),
            "metrics": metrics,
            "metrics_at": p.get("metricsUpdatedAt"),
        })
    posts.sort(key=lambda x: x["date"] or "", reverse=True)

    data = {"updated": now, "profiles": PROFILES, "followers": followers, "posts": posts, "notes": notes}
    SITE.mkdir(exist_ok=True)
    for f in ["index.html"]:
        shutil.copy(HERE / f, SITE / f)
    shutil.copytree(HERE / "assets", SITE / "assets", dirs_exist_ok=True)
    (SITE / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print(f"{len(posts)} posts, followers {followers}, notes {notes}")


if __name__ == "__main__":
    main()
