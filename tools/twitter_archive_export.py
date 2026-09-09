#!/usr/bin/env python3
"""Create sanitized public exports from a Twitter/X archive.

This script reads only data/tweets.js from the archive. It does not parse or
export deleted tweets, direct messages, ads, account-security records, contacts,
IP logs, or device data.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TWITTER_DATE = "%a %b %d %H:%M:%S %z %Y"

EXCLUDED_BY_POLICY = [
    "data/deleted-tweets.js",
    "data/deleted-tweet-headers.js",
    "data/deleted_tweets_media/",
    "data/direct-messages.js",
    "data/direct-messages-group.js",
    "data/direct_messages_media/",
    "data/direct_messages_group_media/",
    "data/ip-audit.js",
    "data/contact.js",
    "data/device-token.js",
    "data/account-creation-ip.js",
    "data/phone-number.js",
    "data/email-address-change.js",
    "data/ad-*",
    "data/like.js",
    "data/follower.js",
    "data/following.js",
]


def load_js_array(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    idx = text.find("=")
    if idx == -1:
        raise ValueError(f"No assignment found in {path}")
    payload = text[idx + 1 :].strip().rstrip(";")
    return json.loads(payload)


def parse_tweet(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("tweet", row)


def clean_text(text: str, urls: list[dict[str, Any]]) -> str:
    cleaned = text or ""
    for url in urls:
        short = url.get("url")
        expanded = url.get("expanded_url") or url.get("display_url")
        if short and expanded:
            cleaned = cleaned.replace(short, expanded)
    return cleaned.strip()


def classify(tweet: dict[str, Any], account_username: str | None) -> str:
    text = tweet.get("full_text") or tweet.get("text") or ""
    if text.startswith("RT @"):
        return "retweet"
    if tweet.get("in_reply_to_status_id_str"):
        if account_username and tweet.get("in_reply_to_screen_name", "").lower() == account_username.lower():
            return "self_thread_reply"
        return "reply"
    if tweet.get("quoted_status_id_str") or tweet.get("quoted_status_permalink"):
        return "quote"
    return "original"


def is_self_repost(tweet: dict[str, Any], account_username: str | None) -> bool:
    if not account_username or classify(tweet, account_username) != "retweet":
        return False
    text = tweet.get("full_text") or tweet.get("text") or ""
    return bool(re.match(rf"^RT @{re.escape(account_username)}\b", text, flags=re.IGNORECASE))


def normalized_match_text(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def self_repost_body(tweet: dict[str, Any], account_username: str) -> str:
    text = normalized_match_text(tweet.get("full_text") or tweet.get("text") or "")
    return re.sub(
        rf"^RT @{re.escape(account_username)}:\s*",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )


def resolve_self_reposts(
    tweets: list[dict[str, Any]],
    account_username: str | None,
) -> tuple[dict[str, list[str]], dict[str, dict[str, Any]], Counter]:
    """Map exact archive repost events back to originals without guessing."""
    if not account_username:
        return {}, {}, Counter()

    originals: list[tuple[str, str]] = []
    for tweet in tweets:
        if classify(tweet, account_username) == "retweet":
            continue
        tweet_id = str(tweet.get("id_str") or tweet.get("id") or "")
        text = normalized_match_text(tweet.get("full_text") or tweet.get("text") or "")
        if tweet_id and text:
            originals.append((tweet_id, text))

    by_source: dict[str, list[str]] = {}
    by_event: dict[str, dict[str, Any]] = {}
    resolution_counts: Counter = Counter()

    for tweet in tweets:
        if not is_self_repost(tweet, account_username):
            continue

        event_id = str(tweet.get("id_str") or tweet.get("id") or "")
        body = self_repost_body(tweet, account_username)
        candidates = [post_id for post_id, text in originals if text == body]
        match_method = "exact"

        if not candidates:
            # X truncates some archived repost text. Match only when the visible
            # prefix identifies one original unambiguously.
            prefix = body.removesuffix("…").removesuffix("...").strip()
            candidates = [
                post_id
                for post_id, text in originals
                if len(prefix) >= 20 and text.startswith(prefix)
            ]
            match_method = "truncated_prefix"

        if len(candidates) == 1:
            source_id = candidates[0]
            by_source.setdefault(source_id, []).append(event_id)
            by_event[event_id] = {
                "source_post_id": source_id,
                "source_match": match_method,
            }
            resolution_counts["resolved"] += 1
        elif len(candidates) > 1:
            by_event[event_id] = {
                "source_post_id": None,
                "source_match": "ambiguous_truncated_prefix",
                "candidate_source_post_ids": candidates,
            }
            resolution_counts["ambiguous"] += 1
        else:
            by_event[event_id] = {
                "source_post_id": None,
                "source_match": "unresolved",
            }
            resolution_counts["unresolved"] += 1

    return by_source, by_event, resolution_counts


def iso_date(created_at: str) -> str:
    dt = datetime.strptime(created_at, TWITTER_DATE)
    return dt.astimezone(timezone.utc).isoformat()


def safe_year(created_at: str) -> str:
    return datetime.strptime(created_at, TWITTER_DATE).strftime("%Y")


def safe_month(created_at_utc: str | None) -> str:
    if not created_at_utc:
        return "undated"
    return datetime.fromisoformat(created_at_utc.replace("Z", "+00:00")).strftime("%Y-%m")


def collect_media(tweet: dict[str, Any]) -> list[dict[str, Any]]:
    entities = tweet.get("extended_entities") or tweet.get("entities") or {}
    media = entities.get("media") or []
    out = []
    for item in media:
        out.append(
            {
                "media_id": item.get("id_str") or item.get("id"),
                "type": item.get("type"),
                "media_url": item.get("media_url_https") or item.get("media_url"),
                "expanded_url": item.get("expanded_url"),
                "display_url": item.get("display_url"),
            }
        )
    return out


def account_username(root: Path) -> str | None:
    path = root / "data" / "account.js"
    if not path.exists():
        return None
    try:
        rows = load_js_array(path)
    except Exception:
        return None
    if not rows:
        return None
    account = rows[0].get("account", {})
    return account.get("username")


def sanitize(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    tweets_path = root / "data" / "tweets.js"
    if not tweets_path.exists():
        raise FileNotFoundError(f"Missing {tweets_path}")

    username = account_username(root)
    rows = load_js_array(tweets_path)
    tweets = [parse_tweet(row) for row in rows]
    reposts_by_source, reposts_by_event, repost_resolution = resolve_self_reposts(
        tweets,
        username,
    )
    records: list[dict[str, Any]] = []
    media_map: list[dict[str, Any]] = []
    counts = Counter()
    years = Counter()
    self_repost_events = 0

    for tweet in tweets:
        created_at = tweet.get("created_at")
        tweet_id = str(tweet.get("id_str") or tweet.get("id") or "")
        entities = tweet.get("entities") or {}
        urls = entities.get("urls") or []
        media = collect_media(tweet)
        kind = classify(tweet, username)
        self_repost = is_self_repost(tweet, username)
        if self_repost:
            self_repost_events += 1

        record = {
            "id": tweet_id,
            "created_at": created_at,
            "created_at_utc": iso_date(created_at) if created_at else None,
            "year": safe_year(created_at) if created_at else None,
            "kind": kind,
            "text": clean_text(tweet.get("full_text") or tweet.get("text") or "", urls),
            "tweet_url": f"https://x.com/{username}/status/{tweet_id}" if username and tweet_id else None,
            "in_reply_to_status_id": tweet.get("in_reply_to_status_id_str"),
            "in_reply_to_screen_name": tweet.get("in_reply_to_screen_name"),
            "quoted_status_id": tweet.get("quoted_status_id_str"),
            "urls": [
                {
                    "expanded_url": u.get("expanded_url"),
                    "display_url": u.get("display_url"),
                }
                for u in urls
            ],
            "media": media,
            "review_status": "pending",
            "canonical_status": "archive_fragment_not_canon",
        }
        if kind == "retweet":
            record["self_repost"] = self_repost
            if self_repost:
                record["repost_source"] = reposts_by_event.get(
                    tweet_id,
                    {"source_post_id": None, "source_match": "unresolved"},
                )
        else:
            event_ids = reposts_by_source.get(tweet_id, [])
            record["self_repost_count"] = len(event_ids)
            record["self_repost_event_ids"] = event_ids
        records.append(record)
        counts[kind] += 1
        if created_at:
            years[record["year"]] += 1
        for item in media:
            media_map.append({"tweet_id": tweet_id, **item})

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Twitter/X archive data/tweets.js only",
        "account_username": username,
        "active_tweets": len(records),
        "kind_counts": dict(counts),
        "repost_meta": {
            "canonical_repost_events": counts["retweet"],
            "canonical_self_repost_events": self_repost_events,
            "distinct_original_posts_self_reposted": len(reposts_by_source),
            "resolved_self_repost_events": repost_resolution["resolved"],
            "ambiguous_self_repost_events": repost_resolution["ambiguous"],
            "unresolved_self_repost_events": repost_resolution["unresolved"],
            "interpretation": (
                "Self-reposts are preserved as recursive resurfacing: a record of ideas "
                "returning, repeating, and gathering meaning across time."
            ),
        },
        "year_counts": dict(sorted(years.items())),
        "media_references": len(media_map),
        "excluded_by_policy": EXCLUDED_BY_POLICY,
        "canonical_note": "Public posts are archival fragments, not canonical essays.",
    }
    return records, media_map, manifest


def markdown_escape(text: str) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return "\n".join(line.rstrip() for line in normalized.split("\n"))


def write_year_markdown(records: list[dict[str, Any]], manifest: dict[str, Any], root: Path) -> None:
    by_year: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_year.setdefault(str(record.get("year") or "undated"), []).append(record)

    index_lines = [
        "# Twitter/X Signal Feed Archive",
        "",
        "This is a sanitized public export of active Twitter/X posts from the official archive",
        "and explicitly approved incremental syncs.",
        "It is preserved as primary source material, not as the canonical expression of developed ideas.",
        "",
        "Essays remain the canonical surface. These posts are archival fragments that can be linked",
        "over time to published work by theme, thread, date, and provenance.",
        "",
        "## Years",
        "",
    ]

    for year in sorted(by_year, reverse=True):
        year_dir = root / year
        year_dir.mkdir(parents=True, exist_ok=True)
        year_records = sorted(
            by_year[year],
            key=lambda r: r.get("created_at_utc") or "",
            reverse=True,
        )
        year_counts = Counter(r.get("kind") for r in year_records)

        index_lines.append(f"- [{year}]({year}/README.md): {len(year_records)} posts")

        lines = [
            f"# Twitter/X Signal Feed Archive: {year}",
            "",
            "These posts are archival source material. They are preserved as contemporaneous",
            "public fragments, not retroactively edited into a coherent narrative.",
            "",
            "## Counts",
            "",
        ]
        for kind, count in sorted(year_counts.items()):
            lines.append(f"- `{kind}`: {count}")
        lines.extend(["", "## Posts", ""])

        current_month = None
        for record in year_records:
            month = safe_month(record.get("created_at_utc"))
            if month != current_month:
                current_month = month
                lines.extend([f"### {month}", ""])

            text = markdown_escape(record.get("text") or "")
            text_block = "\n".join(f"> {line}" if line else ">" for line in text.split("\n"))
            meta = [
                f"- `date`: {record.get('created_at_utc')}",
                f"- `kind`: {record.get('kind')}",
                f"- `canonical_status`: {record.get('canonical_status')}",
            ]
            if record.get("tweet_url"):
                meta.append(f"- `source`: {record.get('tweet_url')}")
            if record.get("media"):
                meta.append(f"- `media_references`: {len(record.get('media') or [])}")
            if record.get("urls"):
                meta.append(f"- `expanded_urls`: {len(record.get('urls') or [])}")
            if record.get("kind") == "retweet":
                meta.append(f"- `self_repost`: {str(bool(record.get('self_repost'))).lower()}")
            elif record.get("self_repost_count"):
                meta.append(f"- `self_repost_count`: {record.get('self_repost_count')}")

            lines.extend([f"#### {record.get('created_at_utc')} / {record.get('kind')}", ""])
            lines.extend(meta)
            lines.extend(["", text_block, "", "---", ""])

        (year_dir / "README.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    index_lines.extend(
        [
            "",
            "## Export Manifest",
            "",
            f"- `generated_at_utc`: {manifest.get('generated_at_utc')}",
            f"- `active_tweets`: {manifest.get('active_tweets')}",
            f"- `canonical_repost_events`: {manifest.get('repost_meta', {}).get('canonical_repost_events')}",
            f"- `canonical_self_repost_events`: {manifest.get('repost_meta', {}).get('canonical_self_repost_events')}",
            f"- `media_references`: {manifest.get('media_references')}",
            "",
            "Deleted posts, direct messages, account security records, IP logs, contacts, ads,",
            "likes, followers, and following records are excluded by policy.",
        ]
    )
    (root / "index.md").write_text("\n".join(index_lines).rstrip() + "\n", encoding="utf-8")


def write_outputs(records: list[dict[str, Any]], media_map: list[dict[str, Any]], manifest: dict[str, Any], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)

    with (out / "tweets.sanitized.jsonl").open("w", encoding="utf-8") as f:
        for record in sorted(
            records,
            key=lambda r: r.get("created_at_utc") or "",
            reverse=True,
        ):
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    with (out / "media-map.json").open("w", encoding="utf-8") as f:
        json.dump(media_map, f, indent=2, ensure_ascii=False)

    with (out / "excluded-summary.json").open("w", encoding="utf-8") as f:
        json.dump({"excluded_by_policy": EXCLUDED_BY_POLICY}, f, indent=2)

    with (out / "export-manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    with (out / "review.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "review_status",
                "year",
                "created_at_utc",
                "kind",
                "id",
                "tweet_url",
                "text_preview",
                "has_media",
                "has_urls",
                "self_repost",
                "self_repost_count",
                "theme",
                "linked_work",
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        for record in sorted(
            records,
            key=lambda r: r.get("created_at_utc") or "",
            reverse=True,
        ):
            preview = re.sub(r"\s+", " ", record.get("text") or "")[:220].strip()
            writer.writerow(
                {
                    "review_status": "pending",
                    "year": record.get("year"),
                    "created_at_utc": record.get("created_at_utc"),
                    "kind": record.get("kind"),
                    "id": record.get("id"),
                    "tweet_url": record.get("tweet_url"),
                    "text_preview": preview,
                    "has_media": bool(record.get("media")),
                    "has_urls": bool(record.get("urls")),
                    "self_repost": bool(record.get("self_repost")),
                    "self_repost_count": record.get("self_repost_count", ""),
                    "theme": "",
                    "linked_work": "",
                }
            )

    write_year_markdown(records, manifest, out.parent)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sanitize a Twitter/X archive into public archive exports.")
    parser.add_argument("archive", type=Path, help="Path to the unzipped Twitter/X archive folder")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("archive/twitter/staging"),
        help="Output directory for sanitized review files",
    )
    args = parser.parse_args()

    records, media_map, manifest = sanitize(args.archive)
    write_outputs(records, media_map, manifest, args.out)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
