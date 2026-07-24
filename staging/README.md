# Twitter / X Review Export

This folder holds the reviewable, machine-readable Twitter/X archive exports generated from the raw archive.

These files are sanitized public-feed artifacts. They are included for search, review, provenance, and future thematic linking, but they are not the preferred human reading surface.

Expected generated files:

- `tweets.sanitized.jsonl`
- `review.csv`
- `media-map.json`
- `excluded-summary.json`
- `export-manifest.json`

Human-readable year exports live one level up:

- `archive/twitter/2025/README.md`
- `archive/twitter/2026/README.md`
- `archive/twitter/index.md`

Run from the repository root:

```bash
python3 tools/twitter_archive_export.py /path/to/twitter-archive-folder
```

For incremental review after this baseline, use:

```bash
python3 tools/twitter_sync.py dry-run \
  --input /path/to/public-post-batch.json
```

The incremental command is dry-run only and writes to the Git-ignored `.twitter-sync/` directory. It does not change these published staging files.
