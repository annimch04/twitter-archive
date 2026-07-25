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

`tools/x_safari_scraper.mjs` and `tools/twitter_sync.py` implement the executable collection and review milestone:

- attaches to Anni's already trusted Safari session;
- scrapes authored public posts after the saved archive cursor;
- reads a public-post batch, canonical JSONL, or Twitter archive `data/tweets.js`;
- rejects deleted-post, direct-message, account-security, IP, contact, device, ad, and social-graph sources;
- compares post IDs with the published sanitized archive;
- stages only unseen public posts;
- writes a local review bundle;
- never changes the published archive.

This is a live collector and dry-run reviewer. It is not a publisher.

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

Before the first run, Safari must allow local page automation:

1. Keep the signed-in `x.com/SayitSalty` profile tab open in Safari.
2. In Safari Settings, enable **Developer > Allow JavaScript from Apple Events**.
3. When macOS asks whether the terminal may automate Safari, approve Safari access.

Then collect new public posts:

```bash
./tools/x_scrape.sh scrape
```

The scraper reads the baseline ID and timestamp from the sanitized archive,
uses only the already open `x.com/SayitSalty` Safari tab, and stops after it
reaches the exact cursor or verifies that multiple authored posts cross the
saved timestamp boundary. The timestamp fallback accounts for X occasionally
omitting an individual post from profile pagination. The saved post ID remains
the inclusion filter.

It does not create a second browser profile, initiate a new X login, read or
export cookies, or request private X surfaces. No API account, bearer token, or
per-post API payment is required.

The collection fails closed if X requires login, reports a temporary limit, or
stops loading before either boundary proof is reached. An incomplete interval is
never presented as a successful review bundle.

The Safari setting permits local Apple Events automation. The scraper narrows
that capability to a tab whose URL contains `x.com/SayitSalty`; keep macOS
Automation permissions limited to tools you trust.

The wrapper finds a normal Node/Python installation or the bundled Codex
runtime. `FIELDLIGHT_NODE` and `FIELDLIGHT_PYTHON` can be set explicitly on
other machines.

Review another collector batch:

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
    "adapter": "trusted_safari_public_profile_scraper",
    "account_username": "SayitSalty",
    "collected_at_utc": "2026-07-23T00:00:00+00:00"
  },
  "posts": []
}
```

Credentials, cookies, browser profiles, and raw service responses do not belong in this repository.

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

1. Complete the first trusted-session scrape and inspect the review bundle.
2. Add a separate, explicit publish command that requires an approved review sheet.
3. Regenerate year pages and manifests after approval.
4. Run the collector on a daily schedule.
5. Link archival fragments to essays by theme and provenance over time.

The scheduler will automate collection and review preparation. It will not automate editorial authority.
