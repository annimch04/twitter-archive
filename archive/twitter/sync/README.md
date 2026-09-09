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

`tools/x_safari_scraper.mjs` and `tools/twitter_sync.py` implement collection,
review, and explicit publication:

- attaches to Anni's already trusted Safari session;
- scrapes authored public posts after the saved archive cursor;
- preserves visible reposts as a separate provisional observation stream;
- reads a public-post batch, canonical JSONL, or Twitter archive `data/tweets.js`;
- rejects deleted-post, direct-message, account-security, IP, contact, device, ad, and social-graph sources;
- compares post IDs with the published sanitized archive;
- stages only unseen public posts;
- writes a local review bundle;
- changes the published archive only through a separate approval command.

Publishing remains a separate, explicit act. The collector never publishes by
itself.

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
omitting an individual post from profile pagination. Pinned and reposted cards
are excluded from authored-post boundary detection because X can surface their
older IDs at the top of the timeline. Reposted cards are still preserved
separately as provisional observations. The saved authored-post ID remains the
inclusion filter.

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
repost-observations.jsonl
review.csv
repost-review.csv
sync-report.json
```

After reviewing a bundle, publish its public posts with explicit approval:

```bash
python3 tools/twitter_sync.py publish \
  --bundle .twitter-sync/review-YYYYMMDDTHHMMSSZ \
  --approve-all
```

This regenerates the sanitized JSONL, review CSV, media map, year pages, index,
manifest, and saved cursor as one operation. It also preserves profile-visible
repost sightings in `archive/twitter/sync/repost-observations.jsonl`. Those
sightings remain provisional and do not increase canonical repost counts.

Approval imports the posts as archival fragments. It does not promote them to
canonical essays or otherwise change their editorial status.

## Public Batch Contract

A collector may provide a JSON list of public post objects, or this envelope:

```json
{
  "schema_version": 2,
  "source": {
    "adapter": "trusted_safari_public_profile_scraper",
    "account_username": "SayitSalty",
    "collected_at_utc": "2026-07-23T00:00:00+00:00"
  },
  "posts": [],
  "repost_observations": []
}
```

Credentials, cookies, browser profiles, and raw service responses do not belong in this repository.

## Repost Semantics

Reposts are selected signal. Self-reposts are especially meaningful in this
archive because they record recursive thinking: an idea returning to the public
surface, repeating across time, or gathering new context.

The downloaded official archive is the source of truth for exact repost event
identity and timing. The public X profile does not expose a distinct event ID
or event timestamp for a reposted card, so the Safari collector records only a
provisional observation tied to the source post.

Within the official export, original posts carry `self_repost_count` and
`self_repost_event_ids` metadata. These fields make recurrence queryable at the
post level. When truncated archive text could refer to more than one original,
the event remains explicitly ambiguous instead of being silently assigned.

That distinction is deliberate:

- an official archive event may increment the canonical repost count;
- a profile sighting can establish that a source post appeared as reposted;
- seeing the same source post on later daily runs does not establish another
  repost action and must not increment the canonical count.

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

1. Reconcile provisional repost observations against the next official archive export.
2. Run the collector on a daily schedule.
3. Link archival fragments and recurring self-reposts to essays by theme and provenance over time.

The scheduler will automate collection and review preparation. It will not automate editorial authority.
