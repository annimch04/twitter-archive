# Incremental Twitter/X Sync

This directory defines the public, review-first process for keeping the Twitter/X archive current.

The collector and the archive are intentionally separate:

```text
public-post collector
        |
        v
dry-run normalization and privacy boundary
        |
        v
local review bundle
        |
        v
human approval
        |
        v
published archival fragments
```

## Current Milestone

`tools/twitter_sync.py` implements the first executable milestone:

- reads a public-post batch, canonical JSONL, or Twitter archive `data/tweets.js`;
- rejects deleted-post, direct-message, account-security, IP, contact, device, ad, and social-graph sources;
- compares post IDs with the published sanitized archive;
- stages only unseen public posts;
- writes a local review bundle;
- never changes the published archive.

This is a dry-run tool, not yet a publisher and not yet a live X collector.

## Commands

From the repository root:

```bash
python3 tools/twitter_sync.py status
```

Review an updated archive export:

```bash
python3 tools/twitter_sync.py dry-run \
  --input /path/to/twitter-archive/data/tweets.js
```

Review a future collector batch:

```bash
python3 tools/twitter_sync.py dry-run \
  --input /path/to/public-post-batch.json
```

Review bundles are written under `.twitter-sync/`, which is intentionally ignored by Git. A bundle contains:

```text
README.md
new-posts.jsonl
review.csv
sync-report.json
```

## Public Batch Contract

A collector may provide a JSON list of public post objects, or this envelope:

```json
{
  "schema_version": 1,
  "source": {
    "adapter": "future-official-api-or-browser-collector",
    "account_username": "SayitSalty",
    "collected_at_utc": "2026-07-23T00:00:00+00:00"
  },
  "posts": []
}
```

Credentials, cookies, browser profiles, API tokens, and raw service responses do not belong in this repository.

## Privacy Boundary

The incremental process is for active public posts only.

It will not import:

- direct messages;
- deleted posts;
- IP or account-security records;
- contacts or device data;
- ad records;
- likes, followers, or following lists.

These are not review categories. They are outside the architecture.

## Next Milestones

1. Add a public-post collector adapter using the most stable permitted source available.
2. Add a separate, explicit publish command that requires an approved review sheet.
3. Regenerate year pages and manifests after approval.
4. Run the collector on a daily schedule.
5. Link archival fragments to essays by theme and provenance over time.

The scheduler will automate collection and review preparation. It will not automate editorial authority.
