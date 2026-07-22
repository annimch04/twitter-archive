# Twitter / X Staging

This folder is for local, reviewable Twitter/X archive exports generated from the raw archive.

Generated files in this folder are intentionally ignored by Git. They may contain thousands of posts and should be reviewed before anything becomes public.

Expected generated files:

- `tweets.sanitized.jsonl`
- `review.csv`
- `media-map.json`
- `excluded-summary.json`
- `export-manifest.json`

Run from the repository root:

```bash
python3 tools/twitter_archive_export.py /path/to/twitter-archive-folder
```
