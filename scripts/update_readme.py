#!/usr/bin/env python3
"""
Auto-update README.md with latest blog post, GitHub activity, and repo count.
Runs as a GitHub Action on a weekly schedule.

Markers in README.md:
  <!-- BLOG:START --> ... <!-- BLOG:END -->
  <!-- ACTIVITY:START --> ... <!-- ACTIVITY:END -->
  <!-- STATS:START --> ... <!-- STATS:END -->
"""

import json
import os
import re
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "napiermd")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
BLOG_URL = os.environ.get("BLOG_URL", "https://napiermd.me").rstrip("/")
README_PATH = "README.md"

RSS_FEED_CANDIDATES = [
    f"{BLOG_URL}/rss/",
    f"{BLOG_URL}/feed",
    f"{BLOG_URL}/feed.xml",
    f"{BLOG_URL}/atom.xml",
    f"{BLOG_URL}/rss.xml",
    f"{BLOG_URL}/blog/feed",
    f"{BLOG_URL}/blog/rss",
]


def fetch_url(url: str, token: str = "") -> bytes | None:
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "napiermd-profile-updater/1.0")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} for {url}")
    except urllib.error.URLError as e:
        print(f"  URL error for {url}: {e.reason}")
    except Exception as e:
        print(f"  fetch failed {url}: {e}")
    return None


def parse_rss_date(date_str: str) -> str:
    """Parse RFC 2822 (RSS) or ISO 8601 (Atom) date into a readable string."""
    if not date_str:
        return ""
    # Try RFC 2822 (RSS pubDate)
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.strftime("%B %d, %Y")
    except Exception:
        pass
    # Try ISO 8601 (Atom updated)
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%B %d, %Y")
    except Exception:
        pass
    # Return first 10 chars as fallback
    return date_str[:10]


def fetch_latest_blog_post() -> str:
    """
    Try each RSS/Atom feed candidate. Return a markdown link string on success,
    empty string if nothing found.
    """
    for feed_url in RSS_FEED_CANDIDATES:
        print(f"  Trying: {feed_url}")
        data = fetch_url(feed_url)
        if not data:
            continue

        try:
            root = ET.fromstring(data)
        except ET.ParseError as e:
            print(f"  XML parse error: {e}")
            continue

        # RSS 2.0: //channel/item
        item = root.find(".//item")
        if item is not None:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            date_str = parse_rss_date((item.findtext("pubDate") or "").strip())
            print(f"  Found RSS post: {title}")
            suffix = f" — {date_str}" if date_str else ""
            return f"[{title}]({link}){suffix}"

        # Atom: //atom:entry
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entry = root.find("atom:entry", ns)
        if entry is None:
            # Some Atom feeds don't namespace at root level
            entry = root.find(".//entry")

        if entry is not None:
            title = (entry.findtext("atom:title", "", ns) or entry.findtext("title") or "").strip()
            link_el = (
                entry.find("atom:link[@rel='alternate']", ns)
                or entry.find("atom:link", ns)
                or entry.find("link")
            )
            link = link_el.get("href", "") if link_el is not None else ""
            updated = (entry.findtext("atom:updated", "", ns) or entry.findtext("updated") or "").strip()
            date_str = parse_rss_date(updated)
            print(f"  Found Atom post: {title}")
            suffix = f" — {date_str}" if date_str else ""
            return f"[{title}]({link}){suffix}"

    print("  No RSS/Atom feed resolved.")
    return ""


def fetch_github_stats() -> tuple[list[str], int]:
    """
    Returns (activity_lines, public_repo_count).
    activity_lines: up to 5 recent public events formatted as markdown.
    """
    # Public repo count from user profile
    repo_count = 0
    user_data = fetch_url(
        f"https://api.github.com/users/{GITHUB_USERNAME}",
        GITHUB_TOKEN,
    )
    if user_data:
        try:
            user = json.loads(user_data)
            repo_count = user.get("public_repos", 0)
        except json.JSONDecodeError:
            pass

    # Recent public events
    events_data = fetch_url(
        f"https://api.github.com/users/{GITHUB_USERNAME}/events/public?per_page=50",
        GITHUB_TOKEN,
    )

    activity_lines: list[str] = []
    seen_repos: set[str] = set()

    if not events_data:
        return activity_lines, repo_count

    try:
        events = json.loads(events_data)
    except json.JSONDecodeError:
        return activity_lines, repo_count

    for event in events:
        if len(activity_lines) >= 5:
            break

        etype = event.get("type", "")
        repo_name = event.get("repo", {}).get("name", "")
        payload = event.get("payload", {})
        created_at = event.get("created_at", "")

        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            date_str = dt.strftime("%b %d")
        except Exception:
            date_str = created_at[:10]

        repo_url = f"https://github.com/{repo_name}"
        repo_short = repo_name.replace(f"{GITHUB_USERNAME}/", "")
        line = None

        if etype == "PushEvent" and repo_name not in seen_repos:
            commits = payload.get("commits", [])
            count = len(commits)
            if count == 0:
                # GitHub omits commit details for pushes from the web UI / merges;
                # skip rather than show "Pushed 0 commits"
                continue
            msg = commits[0].get("message", "").split("\n")[0][:60]
            label = "commit" if count == 1 else "commits"
            line = f"🔨 Pushed {count} {label} to [{repo_short}]({repo_url}) — *{msg}* `{date_str}`"
            seen_repos.add(repo_name)

        elif etype == "CreateEvent" and repo_name not in seen_repos:
            ref_type = payload.get("ref_type", "")
            if ref_type == "repository":
                line = f"📁 Created repo [{repo_short}]({repo_url}) `{date_str}`"
                seen_repos.add(repo_name)
            elif ref_type == "branch":
                ref = payload.get("ref", "")
                line = f"🌿 Created branch `{ref}` in [{repo_short}]({repo_url}) `{date_str}`"
                seen_repos.add(repo_name)

        elif etype == "PullRequestEvent" and repo_name not in seen_repos:
            action = payload.get("action", "")
            pr = payload.get("pull_request", {})
            pr_title = pr.get("title", "")[:60]
            merged = pr.get("merged", False)
            if action == "closed" and merged:
                display_action = "Merged"
            elif action in ("opened", "closed"):
                display_action = action.capitalize()
            else:
                display_action = None
            if display_action:
                line = f"🔀 {display_action} PR in [{repo_short}]({repo_url}): *{pr_title}* `{date_str}`"
                seen_repos.add(repo_name)

        elif etype == "IssuesEvent" and repo_name not in seen_repos:
            action = payload.get("action", "")
            issue_title = (payload.get("issue", {}).get("title") or "")[:60]
            if action in ("opened", "closed"):
                line = f"🐛 {action.capitalize()} issue in [{repo_short}]({repo_url}): *{issue_title}* `{date_str}`"
                seen_repos.add(repo_name)

        elif etype == "ReleaseEvent" and repo_name not in seen_repos:
            release = payload.get("release", {})
            tag = release.get("tag_name", "")
            line = f"🚀 Released `{tag}` in [{repo_short}]({repo_url}) `{date_str}`"
            seen_repos.add(repo_name)

        if line:
            activity_lines.append(line)

    return activity_lines, repo_count


def replace_between_markers(content: str, marker: str, new_content: str) -> str:
    pattern = rf"(<!-- {marker}:START -->).*?(<!-- {marker}:END -->)"
    replacement = rf"\1\n{new_content}\n\2"
    result = re.sub(pattern, replacement, content, flags=re.DOTALL)
    if result == content:
        print(f"  WARNING: marker {marker} not found in README")
    return result


def main() -> None:
    print(f"Updating README for {GITHUB_USERNAME}...")

    with open(README_PATH, "r") as f:
        readme = f.read()

    # --- Blog post ---
    print("\n[Blog post]")
    latest_post = fetch_latest_blog_post()
    if latest_post:
        blog_content = f"> {latest_post}"
    else:
        blog_content = f"> *No posts fetched — check [napiermd.me]({BLOG_URL})*"

    # --- GitHub activity + repo count ---
    print("\n[GitHub activity]")
    activity_lines, repo_count = fetch_github_stats()
    print(f"  Public repos: {repo_count}")
    print(f"  Activity events: {len(activity_lines)}")

    if activity_lines:
        activity_content = "\n".join(activity_lines)
    else:
        activity_content = "*No recent public activity found.*"

    now = datetime.now(timezone.utc).strftime("%B %d, %Y")
    stats_content = f"**{repo_count} public repos** · *Updated {now}*"

    # --- Apply all three replacements ---
    print("\n[Applying markers]")
    readme = replace_between_markers(readme, "BLOG", blog_content)
    readme = replace_between_markers(readme, "ACTIVITY", activity_content)
    readme = replace_between_markers(readme, "STATS", stats_content)

    with open(README_PATH, "w") as f:
        f.write(readme)

    print("\nDone.")


if __name__ == "__main__":
    main()
