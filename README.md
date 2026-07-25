# Twitter / X Public Post Archive

This directory contains a sanitized public archive of Anni McHenry's exported Twitter/X posts.

The archive is primary source material, not public canon.

Posts preserved here are contemporaneous artifacts. They are not retroactively edited into a coherent narrative, and they should not be read as though every post represents the current or final form of an idea. Essays, edited writing, Git history, recordings, proofs, and fieldlight.com reading pages remain the canonical expression of developed work.

Reposts are part of that record, not noise to be removed. In particular, self-reposts make recursive thinking visible: an idea is deliberately brought forward again because it remains active, has gathered new context, or has become legible as part of a repeating pattern.

## Source Principle

The Twitter/X archive belongs in the same provenance chain as handwritten notes, transcripts, Markdown source, Git commits, published essays, recordings, proofs, and reading surfaces.

The purpose is to answer an increasingly important question in an AI-mediated public record:

> Where did this idea come from, and how did it become what it is?

## Published Shape

The public archive is organized as both a reviewable machine-readable export and a human-readable year archive:

```text
archive/twitter/
  README.md
  MIGRATION_PLAN.md
  index.md
  sync/
    README.md
    state.json
  staging/
    tweets.sanitized.jsonl
    review.csv
    media-map.json
    excluded-summary.json
    export-manifest.json
  2025/
    README.md
  2026/
    README.md
```

Year folders contain sanitized active public posts only. Deleted posts, private messages, ad data, account/security data, contacts, IP logs, and device records are excluded by policy.

## Incremental Sync

The initial archive is now also the baseline for a review-first incremental process.

- `sync/state.json` records the current public cursor.
- `tools/x_safari_scraper.mjs` reads new posts from the already trusted Safari session.
- `tools/twitter_sync.py` compares that public-post batch with the cursor and sanitized baseline.
- Authored posts and repost observations remain separate so an observed repost is never mistaken for a newly authored post.
- The official archive remains authoritative for exact repost event counts and timestamps.
- Public-profile repost sightings are provisional and deduplicated by source post; seeing the same card again does not create another repost event.
- New posts are written to a local review bundle under `.twitter-sync/`.
- The dry-run command cannot mutate the published archive.
- Direct messages and other private or excluded sources are rejected before normalization.

See [`sync/README.md`](sync/README.md) for the command, collector contract, privacy boundary, and next milestones.

## Public Artifacts

- `index.md`: entry point into the year-organized archive.
- `2025/README.md` and `2026/README.md`: readable newest-first exports grouped by month.
- `staging/tweets.sanitized.jsonl`: full sanitized active-post feed for tools, search, and future linking.
- `staging/review.csv`: review sheet for theme assignment, linked work, and publication decisions.
- `staging/media-map.json`: media references without copying private archive internals into the reading surface.
- `staging/excluded-summary.json`: policy record of what was intentionally left out.
- `staging/export-manifest.json`: counts and source metadata for the export run.

The export manifest also records `canonical_repost_events` and `canonical_self_repost_events`. These are exact events present in the official account archive. Each original post includes `self_repost_count` and its matched `self_repost_event_ids` when the archive provides enough information to establish the connection. Ambiguous truncated matches are disclosed in the manifest rather than assigned by guesswork. Daily collection may preserve provisional evidence that a source post is currently reposted, but it does not fabricate an event ID, timestamp, or additional recurrence count that X does not expose publicly.

## Relationship to Essays

Over time, posts may be linked to published work by theme. A reading page or essay may eventually include a lineage path such as:

```text
Earlier public fragments
- Tweet - July 2025
- Thread - January 2026
- Notebook - June 2026
- Published essay - July 2026
```

That makes intellectual development visible without flattening every public fragment into the same status as an essay.

Self-reposts add another lineage signal: they show when the author returned to her own earlier language. Over time, these recurrences will be linked by theme alongside ordinary posts, threads, notebooks, essays, recordings, and other published artifacts.

## Public Surface

The public website should not dump the full export into the essay surface. A Twitter/X archive is valuable because it can be comprehensive. A reading surface is valuable because it is curated.

The website layer should therefore show selected pathways, theme collections, and earlier-fragment links into developed work, not an undifferentiated feed of thousands of posts.
