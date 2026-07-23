# Twitter / X Public Post Archive

This directory contains a sanitized public archive of Anni McHenry's exported Twitter/X posts.

The archive is primary source material, not public canon.

Posts preserved here are contemporaneous artifacts. They are not retroactively edited into a coherent narrative, and they should not be read as though every post represents the current or final form of an idea. Essays, edited writing, Git history, recordings, proofs, and fieldlight.com reading pages remain the canonical expression of developed work.

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

## Public Artifacts

- `index.md`: entry point into the year-organized archive.
- `2025/README.md` and `2026/README.md`: readable chronological exports grouped by month.
- `staging/tweets.sanitized.jsonl`: full sanitized active-post feed for tools, search, and future linking.
- `staging/review.csv`: review sheet for theme assignment, linked work, and publication decisions.
- `staging/media-map.json`: media references without copying private archive internals into the reading surface.
- `staging/excluded-summary.json`: policy record of what was intentionally left out.
- `staging/export-manifest.json`: counts and source metadata for the export run.

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

## Public Surface

The public website should not dump the full export into the essay surface. A Twitter/X archive is valuable because it can be comprehensive. A reading surface is valuable because it is curated.

The website layer should therefore show selected pathways, theme collections, and earlier-fragment links into developed work, not an undifferentiated feed of thousands of posts.
