#!/usr/bin/env python3
"""Collect, review, and explicitly publish incremental public X posts.

Collection is review-first. It compares a collector batch with the current
sanitized archive, rejects private source classes, and writes a local review
bundle containing only new public posts. Publishing is a separate command and
requires an explicit human-approval flag.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from twitter_archive_export import (
    EXCLUDED_BY_POLICY,
    classify,
    clean_text,
    collect_media,
    iso_date,
    load_js_array,
    parse_tweet,
    safe_year,
    write_outputs,
)

DEFAULT_BASELINE = Path("archive/twitter/staging/tweets.sanitized.jsonl")
DEFAULT_STATE = Path("archive/twitter/sync/state.json")
DEFAULT_MANIFEST = Path("archive/twitter/staging/export-manifest.json")
DEFAULT_MEDIA_MAP = Path("archive/twitter/staging/media-map.json")
DEFAULT_REPOST_OBSERVATIONS = Path("archive/twitter/sync/repost-observations.jsonl")
DEFAULT_API_BASE = "https://api.x.com/2"
DEFAULT_TOKEN_ENV = "X_BEARER_TOKEN"

FORBIDDEN_EXACT_NAMES = {
    "deleted-tweets.js",
    "deleted-tweet-headers.js",
    "direct-messages.js",
    "direct-messages-group.js",
    "ip-audit.js",
    "contact.js",
    "device-token.js",
    "account-creation-ip.js",
    "phone-number.js",
    "email-address-change.js",
    "like.js",
    "follower.js",
    "following.js",
}
FORBIDDEN_PATH_MARKERS = {
    "deleted_tweets_media",
    "direct_messages_media",
    "direct_messages_group_media",
}
PRIVATE_RECORD_KEYS = {
    "dmConversation",
    "directMessage",
    "directMessages",
    "messageCreate",
    "messageData",
}


class PrivacyBoundaryError(ValueError):
    """Raised when an input may contain intentionally excluded private data."""


class XApiError(RuntimeError):
    """Raised when the public-post collector cannot complete an X API request."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def assert_public_source(path: Path) -> None:
    lowered_parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    if name in FORBIDDEN_EXACT_NAMES or name.startswith("ad-"):
        raise PrivacyBoundaryError(f"Refusing excluded source: {path}")
    if lowered_parts & FORBIDDEN_PATH_MARKERS:
        raise PrivacyBoundaryError(f"Refusing private media source: {path}")
    if path.suffix.lower() == ".js" and name != "tweets.js":
        raise PrivacyBoundaryError(
            "JavaScript imports are restricted to the active public data/tweets.js file."
        )


def assert_public_record(row: dict[str, Any]) -> None:
    private_keys = PRIVATE_RECORD_KEYS.intersection(row)
    if private_keys:
        names = ", ".join(sorted(private_keys))
        raise PrivacyBoundaryError(f"Private message record shape detected: {names}")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} is not a JSON object")
        rows.append(value)
    return rows


def load_input(
    path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    assert_public_source(path)
    suffix = path.suffix.lower()
    metadata: dict[str, Any] = {"source_path": str(path), "adapter": suffix.lstrip(".")}
    repost_observations: list[dict[str, Any]] = []

    if suffix == ".js":
        rows = load_js_array(path)
        metadata["adapter"] = "twitter_archive_tweets_js"
    elif suffix == ".jsonl":
        rows = load_jsonl(path)
        metadata["adapter"] = "public_jsonl"
    elif suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict) and isinstance(payload.get("posts"), list):
            rows = payload["posts"]
            observations = payload.get("repost_observations")
            if observations is not None:
                if not isinstance(observations, list):
                    raise ValueError("repost_observations must be a list.")
                repost_observations = observations
            source = payload.get("source")
            if isinstance(source, dict):
                metadata["collector"] = source
        elif isinstance(payload, dict) and isinstance(payload.get("tweets"), list):
            rows = payload["tweets"]
        else:
            raise ValueError("JSON input must be a list or contain a posts/tweets list.")
        metadata["adapter"] = "public_json"
    else:
        raise ValueError("Supported inputs are .js, .json, and .jsonl public-post files.")

    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Every input record must be a JSON object.")
        assert_public_record(row)
    for observation in repost_observations:
        if not isinstance(observation, dict):
            raise ValueError("Every repost observation must be a JSON object.")
        assert_public_record(observation)
        validate_repost_observation(observation)
    return rows, repost_observations, metadata


def load_baseline(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Baseline archive not found: {path}")
    return load_jsonl(path)


def canonical_record(row: dict[str, Any], username: str) -> dict[str, Any]:
    assert_public_record(row)

    if row.get("canonical_status") == "archive_fragment_not_canon":
        record = dict(row)
        record["review_status"] = "pending"
        validate_record(record)
        return record

    tweet = parse_tweet(row)
    assert_public_record(tweet)
    tweet_id = str(tweet.get("id_str") or tweet.get("id") or "").strip()
    created_at = tweet.get("created_at")
    if not tweet_id or not created_at:
        raise ValueError("Public post is missing id/id_str or created_at.")

    entities = tweet.get("entities") or {}
    urls = entities.get("urls") or []
    media = collect_media(tweet)
    record = {
        "id": tweet_id,
        "created_at": created_at,
        "created_at_utc": iso_date(created_at),
        "year": safe_year(created_at),
        "kind": classify(tweet, username),
        "text": clean_text(tweet.get("full_text") or tweet.get("text") or "", urls),
        "tweet_url": f"https://x.com/{username}/status/{tweet_id}",
        "in_reply_to_status_id": tweet.get("in_reply_to_status_id_str"),
        "in_reply_to_screen_name": tweet.get("in_reply_to_screen_name"),
        "quoted_status_id": tweet.get("quoted_status_id_str"),
        "urls": [
            {
                "expanded_url": item.get("expanded_url"),
                "display_url": item.get("display_url"),
            }
            for item in urls
        ],
        "media": media,
        "review_status": "pending",
        "canonical_status": "archive_fragment_not_canon",
    }
    validate_record(record)
    return record


def validate_record(record: dict[str, Any]) -> None:
    required = ("id", "created_at_utc", "year", "kind", "text", "canonical_status")
    missing = [name for name in required if record.get(name) is None or record.get(name) == ""]
    if missing:
        raise ValueError(f"Sanitized record is missing required fields: {', '.join(missing)}")
    if record.get("canonical_status") != "archive_fragment_not_canon":
        raise ValueError("Incremental posts must remain archive fragments, not public canon.")
    datetime.fromisoformat(str(record["created_at_utc"]).replace("Z", "+00:00"))


def validate_repost_observation(observation: dict[str, Any]) -> None:
    required = (
        "observation_id",
        "observation_kind",
        "recurrence_key",
        "observed_at_utc",
        "source_post",
        "self_repost",
        "canonical_status",
        "provenance",
    )
    missing = [
        name
        for name in required
        if observation.get(name) is None or observation.get(name) == ""
    ]
    if missing:
        raise ValueError(f"Repost observation is missing required fields: {', '.join(missing)}")
    if observation["observation_kind"] != "repost":
        raise ValueError("Only public repost observations are accepted.")
    if observation["canonical_status"] != "provisional_profile_observation":
        raise ValueError("Profile observations must remain provisional until archive reconciliation.")
    source_post = observation["source_post"]
    if not isinstance(source_post, dict) or not source_post.get("id"):
        raise ValueError("Repost observation is missing its source post identity.")
    datetime.fromisoformat(str(observation["observed_at_utc"]).replace("Z", "+00:00"))


def archive_cursor(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {"post_count": 0, "last_archived_post_id": None, "last_archived_at_utc": None}
    latest = max(
        records,
        key=lambda row: (
            str(row.get("created_at_utc") or ""),
            int(str(row.get("id") or "0")),
        ),
    )
    return {
        "post_count": len(records),
        "last_archived_post_id": latest.get("id"),
        "last_archived_at_utc": latest.get("created_at_utc"),
    }


def x_api_get(
    path: str,
    params: dict[str, Any],
    token: str,
    api_base: str = DEFAULT_API_BASE,
) -> dict[str, Any]:
    """Read a public X API resource without persisting credentials or raw responses."""
    query = urlencode({key: value for key, value in params.items() if value is not None})
    url = f"{api_base.rstrip('/')}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{query}"
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "fieldlight-public-writing-twitter-sync/1.0",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.reason
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = body.get("detail") or body.get("title") or detail
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        raise XApiError(f"X API returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise XApiError(f"Could not reach X API: {exc.reason}") from exc

    if not isinstance(payload, dict):
        raise XApiError("X API returned an unexpected response shape.")
    if payload.get("errors") and not payload.get("data"):
        raise XApiError(f"X API request failed: {payload['errors']}")
    return payload


def resolve_x_user_id(
    username: str,
    token: str,
    api_base: str = DEFAULT_API_BASE,
    request_json: Any = x_api_get,
) -> str:
    payload = request_json(
        f"users/by/username/{username}",
        {},
        token,
        api_base,
    )
    user = payload.get("data") or {}
    user_id = str(user.get("id") or "").strip()
    if not user_id:
        raise XApiError(f"Could not resolve X user ID for @{username}.")
    return user_id


def classify_x_api_post(post: dict[str, Any], user_id: str) -> str:
    references = post.get("referenced_tweets") or []
    reference_types = {item.get("type") for item in references}
    if "retweeted" in reference_types:
        return "retweet"
    if "replied_to" in reference_types or post.get("in_reply_to_user_id"):
        if str(post.get("in_reply_to_user_id") or "") == user_id:
            return "self_thread_reply"
        return "reply"
    if "quoted" in reference_types:
        return "quote"
    return "original"


def normalize_x_api_post(
    post: dict[str, Any],
    username: str,
    user_id: str,
    media_by_key: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    post_id = str(post.get("id") or "").strip()
    created_at = str(post.get("created_at") or "").strip()
    if not post_id or not created_at:
        raise XApiError("X API post is missing id or created_at.")

    created = datetime.fromisoformat(created_at.replace("Z", "+00:00")).astimezone(timezone.utc)
    entities = post.get("entities") or {}
    urls = entities.get("urls") or []
    references = post.get("referenced_tweets") or []
    reference_ids = {item.get("type"): str(item.get("id")) for item in references if item.get("id")}
    attachments = post.get("attachments") or {}
    media = []
    for media_key in attachments.get("media_keys") or []:
        item = media_by_key.get(media_key, {})
        media.append(
            {
                "media_id": item.get("media_key") or media_key,
                "type": item.get("type"),
                "media_url": item.get("url") or item.get("preview_image_url"),
                "expanded_url": None,
                "display_url": None,
            }
        )

    record = {
        "id": post_id,
        "created_at": created_at,
        "created_at_utc": created.isoformat(),
        "year": created.strftime("%Y"),
        "kind": classify_x_api_post(post, user_id),
        "text": clean_text(post.get("text") or "", urls),
        "tweet_url": f"https://x.com/{username}/status/{post_id}",
        "in_reply_to_status_id": reference_ids.get("replied_to"),
        "in_reply_to_screen_name": username
        if str(post.get("in_reply_to_user_id") or "") == user_id
        else None,
        "quoted_status_id": reference_ids.get("quoted"),
        "urls": [
            {
                "expanded_url": item.get("expanded_url"),
                "display_url": item.get("display_url"),
            }
            for item in urls
        ],
        "media": media,
        "review_status": "pending",
        "canonical_status": "archive_fragment_not_canon",
    }
    validate_record(record)
    return record


def collect_x_posts(
    username: str,
    since_id: str,
    token: str,
    user_id: str | None = None,
    api_base: str = DEFAULT_API_BASE,
    request_json: Any = x_api_get,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Collect authored public posts after the baseline cursor, including replies."""
    resolved_user_id = user_id or resolve_x_user_id(
        username,
        token,
        api_base=api_base,
        request_json=request_json,
    )
    posts: list[dict[str, Any]] = []
    page_count = 0
    pagination_token: str | None = None

    while True:
        params = {
            "since_id": since_id,
            "max_results": 100,
            "tweet.fields": (
                "id,text,created_at,conversation_id,in_reply_to_user_id,"
                "referenced_tweets,entities,attachments"
            ),
            "expansions": "attachments.media_keys",
            "media.fields": "media_key,type,url,preview_image_url,width,height,alt_text",
            "pagination_token": pagination_token,
        }
        payload = request_json(
            f"users/{resolved_user_id}/tweets",
            params,
            token,
            api_base,
        )
        page_count += 1
        includes = payload.get("includes") or {}
        media_by_key = {
            str(item.get("media_key")): item
            for item in includes.get("media") or []
            if item.get("media_key")
        }
        for post in payload.get("data") or []:
            posts.append(normalize_x_api_post(post, username, resolved_user_id, media_by_key))

        meta = payload.get("meta") or {}
        pagination_token = meta.get("next_token")
        if not pagination_token:
            break

    source = {
        "adapter": "official_x_api_v2_user_posts",
        "account_username": username,
        "account_user_id": resolved_user_id,
        "collected_at_utc": utc_now(),
        "since_id": since_id,
        "pages_read": page_count,
        "public_posts_returned": len(posts),
        "private_sources_requested": False,
    }
    return posts, source


def stage(
    rows: list[dict[str, Any]],
    baseline: list[dict[str, Any]],
    username: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    baseline_ids = {str(row.get("id")) for row in baseline if row.get("id")}
    seen_input: set[str] = set()
    new_records: list[dict[str, Any]] = []
    duplicate_count = 0

    for row in rows:
        record = canonical_record(row, username)
        post_id = str(record["id"])
        if post_id in baseline_ids or post_id in seen_input:
            duplicate_count += 1
            continue
        seen_input.add(post_id)
        new_records.append(record)

    new_records.sort(key=lambda row: (str(row["created_at_utc"]), int(str(row["id"]))))
    report = {
        "schema_version": 1,
        "mode": "dry_run_review_only",
        "generated_at_utc": utc_now(),
        "account_username": username,
        "input_records": len(rows),
        "baseline_records": len(baseline),
        "duplicate_records": duplicate_count,
        "new_public_records": len(new_records),
        "new_kind_counts": dict(Counter(row["kind"] for row in new_records)),
        "baseline_cursor": archive_cursor(baseline),
        "proposed_cursor": archive_cursor(baseline + new_records),
        "privacy_policy": {
            "direct_messages": "hard_rejected",
            "deleted_posts": "hard_rejected",
            "account_security_and_ip_data": "hard_rejected",
            "automatic_publication": False,
        },
    }
    return new_records, report


def stage_repost_observations(
    observations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    staged: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicate_count = 0

    for observation in observations:
        validate_repost_observation(observation)
        observation_id = str(observation["observation_id"])
        if observation_id in seen:
            duplicate_count += 1
            continue
        seen.add(observation_id)
        staged.append(dict(observation))

    staged.sort(
        key=lambda row: (
            str(row.get("observed_at_utc") or ""),
            str((row.get("source_post") or {}).get("id") or ""),
        )
    )
    return staged, {
        "input_repost_observations": len(observations),
        "duplicate_repost_observations": duplicate_count,
        "provisional_repost_observations": len(staged),
        "provisional_self_repost_source_count": len(
            {
                str((row.get("source_post") or {}).get("id"))
                for row in staged
                if row.get("self_repost")
            }
        ),
    }


def self_repost_meta(
    baseline: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    username: str,
) -> dict[str, Any]:
    canonical_count = sum(
        1
        for row in baseline
        if row.get("kind") == "retweet"
        and re.match(
            rf"^RT @{re.escape(username)}\b",
            str(row.get("text") or ""),
            flags=re.IGNORECASE,
        )
    )
    provisional_sources = {
        str((row.get("source_post") or {}).get("id"))
        for row in observations
        if row.get("self_repost")
    }
    provisional_sources.discard("None")
    return {
        "canonical_event_count_in_official_archive": canonical_count,
        "provisional_distinct_source_count_in_batch": len(provisional_sources),
        "counting_policy": (
            "Canonical events and provisional profile observations remain separate. "
            "Repeated daily sightings do not increment the number of repost actions."
        ),
        "interpretation": (
            "Self-reposts are preserved as recursive thinking: ideas deliberately "
            "resurfaced so their recurrence and changing context remain visible."
        ),
    }


def write_review_bundle(
    output_dir: Path,
    records: list[dict[str, Any]],
    repost_observations: list[dict[str, Any]],
    report: dict[str, Any],
    source: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)

    with (output_dir / "new-posts.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    with (output_dir / "repost-observations.jsonl").open("w", encoding="utf-8") as handle:
        for observation in repost_observations:
            handle.write(json.dumps(observation, ensure_ascii=False) + "\n")

    with (output_dir / "review.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "decision",
            "created_at_utc",
            "kind",
            "id",
            "tweet_url",
            "text_preview",
            "has_media",
            "theme",
            "linked_work",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "decision": "pending",
                    "created_at_utc": record["created_at_utc"],
                    "kind": record["kind"],
                    "id": record["id"],
                    "tweet_url": record.get("tweet_url"),
                    "text_preview": re.sub(r"\s+", " ", record.get("text") or "")[:220],
                    "has_media": bool(record.get("media")),
                    "theme": "",
                    "linked_work": "",
                }
            )

    with (output_dir / "repost-review.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "decision",
            "observed_at_utc",
            "self_repost",
            "source_author",
            "source_post_id",
            "source_url",
            "text_preview",
            "theme",
            "linked_work",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for observation in repost_observations:
            source_post = observation.get("source_post") or {}
            writer.writerow(
                {
                    "decision": "pending",
                    "observed_at_utc": observation["observed_at_utc"],
                    "self_repost": observation["self_repost"],
                    "source_author": source_post.get("author_username"),
                    "source_post_id": source_post.get("id"),
                    "source_url": source_post.get("url"),
                    "text_preview": re.sub(r"\s+", " ", source_post.get("text") or "")[:220],
                    "theme": "",
                    "linked_work": "",
                }
            )

    full_report = dict(report)
    full_report["source"] = source
    (output_dir / "sync-report.json").write_text(
        json.dumps(full_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "README.md").write_text(
        "# Twitter/X Incremental Review Bundle\n\n"
        "This is a dry-run artifact. Nothing in this folder has been added to the\n"
        "published archive. Review `review.csv` before a separate publish step is\n"
        "ever introduced or run. `repost-review.csv` holds public-profile repost\n"
        "observations separately because X does not expose their event IDs or exact\n"
        "repost times on the profile surface.\n\n"
        "Self-reposts are retained as evidence of recursive thinking and deliberate\n"
        "resurfacing. Repeated daily sightings do not count as new repost actions.\n\n"
        "Direct messages, deleted posts, IP/security data, contacts, device records,\n"
        "ads, likes, followers, and following records are outside this pipeline.\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"Required archive file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def merge_media_map(
    existing: list[dict[str, Any]],
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = list(existing)
    seen = {
        json.dumps(item, sort_keys=True, ensure_ascii=False)
        for item in existing
    }
    for record in records:
        for item in record.get("media") or []:
            mapped = {"tweet_id": str(record["id"]), **item}
            key = json.dumps(mapped, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                seen.add(key)
                merged.append(mapped)
    return merged


def publish_review_bundle(
    bundle: Path,
    baseline_path: Path = DEFAULT_BASELINE,
    manifest_path: Path = DEFAULT_MANIFEST,
    media_map_path: Path = DEFAULT_MEDIA_MAP,
    state_path: Path = DEFAULT_STATE,
    observations_path: Path = DEFAULT_REPOST_OBSERVATIONS,
) -> dict[str, Any]:
    """Publish one explicitly approved review bundle into the public archive."""
    report = read_json(bundle / "sync-report.json")
    if report.get("mode") != "dry_run_review_only":
        raise ValueError("The bundle is not a review-only Twitter/X sync artifact.")

    baseline = load_baseline(baseline_path)
    baseline_ids = {str(record.get("id")) for record in baseline}
    proposed = load_jsonl(bundle / "new-posts.jsonl")
    if len(proposed) != int(report.get("new_public_records") or 0):
        raise ValueError(
            "The review bundle no longer matches its sync report; collect a fresh bundle."
        )
    accepted: list[dict[str, Any]] = []
    already_present: list[dict[str, Any]] = []
    seen_new: set[str] = set()
    for record in proposed:
        validate_record(record)
        post_id = str(record["id"])
        if post_id in seen_new:
            raise ValueError(f"The review bundle repeats public post ID {post_id}.")
        seen_new.add(post_id)
        if post_id in baseline_ids:
            already_present.append(record)
            continue
        accepted.append(record)

    # A publish can be resumed safely if generated staging files were written but
    # the state cursor was not. IDs already present are never duplicated.
    if not proposed:
        raise ValueError("The approved bundle contains no public posts.")

    merged_records = baseline + accepted
    cursor = archive_cursor(merged_records)
    prior_state = read_json(state_path) if state_path.exists() else {}
    if (
        not accepted
        and prior_state.get("last_archived_post_id") == cursor["last_archived_post_id"]
        and prior_state.get("last_archived_at_utc") == cursor["last_archived_at_utc"]
    ):
        current_manifest = read_json(manifest_path)
        return {
            "published_public_posts": len(proposed),
            "newly_added_posts": 0,
            "posts_already_present_during_resume": len(already_present),
            "already_published": True,
            "active_tweets": len(merged_records),
            "provisional_repost_observations": int(
                (prior_state.get("last_publish") or {}).get(
                    "provisional_repost_observations", 0
                )
            ),
            "media_references": current_manifest.get("media_references"),
            **cursor,
        }

    manifest = read_json(manifest_path)
    media_map = merge_media_map(read_json(media_map_path), proposed)
    counts = Counter(str(record.get("kind")) for record in merged_records)
    years = Counter(str(record.get("year")) for record in merged_records)
    published_at = utc_now()
    collector = ((report.get("source") or {}).get("collector") or {})

    manifest.update(
        {
            "generated_at_utc": published_at,
            "source": (
                "Twitter/X official archive plus approved public-profile "
                "incremental sync"
            ),
            "active_tweets": len(merged_records),
            "kind_counts": dict(sorted(counts.items())),
            "year_counts": dict(sorted(years.items())),
            "media_references": len(media_map),
            "excluded_by_policy": EXCLUDED_BY_POLICY,
            "incremental_sync": {
                "latest_batch_published_at_utc": published_at,
                "latest_batch_collected_at_utc": collector.get("collected_at_utc"),
                "latest_batch_new_posts": len(proposed),
                "source_adapter": collector.get("adapter"),
                "approval": "explicit_human_approval",
                "canonical_status": "archive_fragment_not_canon",
            },
        }
    )

    incoming_observations = load_jsonl(bundle / "repost-observations.jsonl")
    existing_observations = (
        load_jsonl(observations_path) if observations_path.exists() else []
    )
    observations_by_id = {
        str(item["observation_id"]): item for item in existing_observations
    }
    for observation in incoming_observations:
        validate_repost_observation(observation)
        observations_by_id.setdefault(str(observation["observation_id"]), observation)

    # Validate every artifact before the first published file is regenerated.
    for record in merged_records:
        validate_record(record)
    for observation in existing_observations:
        validate_repost_observation(observation)

    staging_dir = baseline_path.parent
    write_outputs(merged_records, media_map, manifest, staging_dir)

    observations_path.parent.mkdir(parents=True, exist_ok=True)
    with observations_path.open("w", encoding="utf-8") as handle:
        for observation in sorted(
            observations_by_id.values(),
            key=lambda item: (
                str(item.get("observed_at_utc") or ""),
                str(item.get("observation_id") or ""),
            ),
        ):
            handle.write(json.dumps(observation, ensure_ascii=False) + "\n")

    state = {
        "schema_version": 2,
        "account_username": report.get("account_username") or "SayitSalty",
        "source_manifest": str(manifest_path),
        **cursor,
        "publication_mode": "human_review_required",
        "private_data_policy": "excluded_by_architecture",
        "last_publish": {
            "published_at_utc": published_at,
            "new_public_posts": len(proposed),
            "provisional_repost_observations": len(incoming_observations),
            "source_adapter": collector.get("adapter"),
        },
    }
    state_path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "published_public_posts": len(proposed),
        "newly_added_posts": len(accepted),
        "posts_already_present_during_resume": len(already_present),
        "active_tweets": len(merged_records),
        "provisional_repost_observations": len(incoming_observations),
        "media_references": len(media_map),
        **cursor,
    }


def default_output_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(".twitter-sync") / f"review-{stamp}"


def status_command(args: argparse.Namespace) -> int:
    baseline = load_baseline(args.baseline)
    current = archive_cursor(baseline)
    if args.state.exists():
        current["persisted_state"] = json.loads(args.state.read_text(encoding="utf-8"))
    print(json.dumps(current, indent=2))
    return 0


def dry_run_command(args: argparse.Namespace) -> int:
    rows, observations, source = load_input(args.input)
    baseline = load_baseline(args.baseline)
    records, report = stage(rows, baseline, args.username)
    staged_observations, observation_report = stage_repost_observations(observations)
    report.update(observation_report)
    report["self_repost_meta"] = self_repost_meta(
        baseline, staged_observations, args.username
    )
    write_review_bundle(
        args.output_dir, records, staged_observations, report, source
    )
    print(json.dumps({**report, "review_bundle": str(args.output_dir)}, indent=2))
    return 0


def collect_command(args: argparse.Namespace) -> int:
    token = os.environ.get(args.token_env, "").strip()
    if not token:
        raise XApiError(
            f"Missing {args.token_env}. Set an X API bearer token in the environment; "
            "tokens are never read from or written to the repository."
        )

    baseline = load_baseline(args.baseline)
    cursor = archive_cursor(baseline)
    since_id = str(cursor.get("last_archived_post_id") or "").strip()
    if not since_id:
        raise ValueError("The baseline has no last archived post ID for incremental collection.")

    rows, source = collect_x_posts(
        username=args.username,
        since_id=since_id,
        token=token,
        user_id=args.user_id,
        api_base=args.api_base,
    )
    records, report = stage(rows, baseline, args.username)
    staged_observations, observation_report = stage_repost_observations([])
    report.update(observation_report)
    report["self_repost_meta"] = self_repost_meta(
        baseline, staged_observations, args.username
    )
    write_review_bundle(
        args.output_dir, records, staged_observations, report, source
    )
    print(json.dumps({**report, "source": source, "review_bundle": str(args.output_dir)}, indent=2))
    return 0


def scrape_command(args: argparse.Namespace) -> int:
    baseline = load_baseline(args.baseline)
    cursor = archive_cursor(baseline)
    since_id = str(cursor.get("last_archived_post_id") or "").strip()
    if not since_id:
        raise ValueError("The baseline has no last archived post ID for incremental scraping.")
    cutoff = str(cursor.get("last_archived_at_utc") or "").strip()
    if not cutoff:
        raise ValueError("The baseline has no archive timestamp for incremental scraping.")

    scraper = Path(__file__).with_name("x_safari_scraper.mjs")
    with tempfile.TemporaryDirectory(prefix="fieldlight-x-scrape-") as temp:
        scraped = Path(temp) / "public-posts.json"
        command = [
            args.node,
            str(scraper),
            "--username",
            args.username,
            "--cursor",
            since_id,
            "--cutoff",
            cutoff,
            "--output",
            str(scraped),
            "--max-scrolls",
            str(args.max_scrolls),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise ValueError(f"Node executable not found: {args.node}") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            if "Safari blocks local page automation" in detail:
                raise ValueError(
                    "Safari blocks local page automation. In Safari Settings, enable "
                    "Developer > Allow JavaScript from Apple Events, then run the scraper again."
                ) from exc
            raise ValueError(
                f"The public-profile scraper did not complete: {detail or 'unknown error'}"
            ) from exc

        rows, observations, source = load_input(scraped)
        records, report = stage(rows, baseline, args.username)
        staged_observations, observation_report = stage_repost_observations(observations)
        report.update(observation_report)
        report["self_repost_meta"] = self_repost_meta(
            baseline, staged_observations, args.username
        )
        write_review_bundle(
            args.output_dir, records, staged_observations, report, source
        )

    print(json.dumps({**report, "source": source, "review_bundle": str(args.output_dir)}, indent=2))
    return 0


def publish_command(args: argparse.Namespace) -> int:
    if not args.approve_all:
        raise ValueError(
            "Publishing requires --approve-all to record explicit human approval."
        )
    result = publish_review_bundle(
        bundle=args.bundle,
        baseline_path=args.baseline,
        manifest_path=args.manifest,
        media_map_path=args.media_map,
        state_path=args.state,
        observations_path=args.repost_observations,
    )
    print(json.dumps(result, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect and review public X posts, then publish only with approval."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Show the published archive cursor.")
    status_parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    status_parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    status_parser.set_defaults(func=status_command)

    dry_parser = subparsers.add_parser("dry-run", help="Build a review-only incremental bundle.")
    dry_parser.add_argument("--input", required=True, type=Path)
    dry_parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    dry_parser.add_argument("--output-dir", type=Path, default=None)
    dry_parser.add_argument("--username", default="SayitSalty")
    dry_parser.set_defaults(func=dry_run_command)

    collect_parser = subparsers.add_parser(
        "collect",
        help="Fetch public posts newer than the archive cursor and build a review bundle.",
    )
    collect_parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    collect_parser.add_argument("--output-dir", type=Path, default=None)
    collect_parser.add_argument("--username", default="SayitSalty")
    collect_parser.add_argument("--user-id", default=None)
    collect_parser.add_argument("--token-env", default=DEFAULT_TOKEN_ENV)
    collect_parser.add_argument("--api-base", default=DEFAULT_API_BASE, help=argparse.SUPPRESS)
    collect_parser.set_defaults(func=collect_command)

    scrape_parser = subparsers.add_parser(
        "scrape",
        help=(
            "Scrape authored public posts and provisional repost observations "
            "into a review bundle."
        ),
    )
    scrape_parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    scrape_parser.add_argument("--output-dir", type=Path, default=None)
    scrape_parser.add_argument("--username", default="SayitSalty")
    scrape_parser.add_argument("--max-scrolls", type=int, default=80)
    scrape_parser.add_argument("--node", default="node")
    scrape_parser.set_defaults(func=scrape_command)

    publish_parser = subparsers.add_parser(
        "publish",
        help="Publish an approved review bundle and regenerate the public archive.",
    )
    publish_parser.add_argument("--bundle", required=True, type=Path)
    publish_parser.add_argument("--approve-all", action="store_true")
    publish_parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    publish_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    publish_parser.add_argument("--media-map", type=Path, default=DEFAULT_MEDIA_MAP)
    publish_parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    publish_parser.add_argument(
        "--repost-observations",
        type=Path,
        default=DEFAULT_REPOST_OBSERVATIONS,
    )
    publish_parser.set_defaults(func=publish_command)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "output_dir", None) is None and args.command in {
        "dry-run",
        "collect",
        "scrape",
    }:
        args.output_dir = default_output_dir()
    try:
        return args.func(args)
    except (
        PrivacyBoundaryError,
        XApiError,
        ValueError,
        FileNotFoundError,
        json.JSONDecodeError,
    ) as exc:
        print(f"twitter-sync: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
