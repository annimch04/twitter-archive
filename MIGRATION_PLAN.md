# Twitter / X Archive Migration Plan

Archive export reviewed: 2026-07-22.

## Export Summary

The local Twitter/X archive is separable enough to migrate safely without publishing private or deleted material.

Useful public-feed sources:

- `data/tweets.js`: main active tweet archive
- `data/tweet-headers.js`: lightweight tweet index/header data
- `data/tweets_media/`: media attached to active tweets
- `data/profile.js`: public-facing profile/avatar/header data, if needed

Do not publish by default:

- `data/deleted-tweets.js`
- `data/deleted-tweet-headers.js`
- `data/deleted_tweets_media/`
- `data/direct-messages.js`
- `data/direct-messages-group.js`
- `data/direct_messages_media/`
- `data/direct_messages_group_media/`
- `data/ip-audit.js`
- `data/contact.js`
- `data/device-token.js`
- `data/account-creation-ip.js`
- `data/phone-number.js`
- `data/email-address-change.js`
- `data/ad-*`
- `data/like.js`, `data/follower.js`, and `data/following.js`, unless separately reviewed for context

Observed counts from the 2026-07-22 archive:

- Active tweets: 2,948
- Deleted tweets: 18
- Retweets: 94
- Originals/top-level posts: 1,194
- Replies to others: 1,370
- Likely self-thread replies: 290
- Tweets with media: 243
- Media files in `tweets_media`: 275
- Tweets with URLs: 156
- Date span: 2025-2026

## Migration Rule

Do not migrate the archive as a raw export. Migrate it as a sanitized public signal feed.

The raw export may contain private account data, deleted posts, direct messages, IP records, ad records, contacts, and other material that has no place in a public writing repository.

## Pipeline

1. Parse only `data/tweets.js`.
2. Exclude deleted tweet files entirely.
3. Exclude DMs, ads, account/security records, IP logs, contacts, and device data entirely.
4. Classify active posts as:
   - original posts
   - replies
   - likely self-thread replies
   - retweets
   - quote/linked posts
   - media posts
5. Generate a reviewable local staging export.
6. Review inclusion rules before committing any post archive to Git.
7. Publish only sanitized records into year/theme folders.
8. Link selected public fragments to essays and reading pages over time.

## Staging Bundle

The first generated artifact should be local review material:

```text
archive/twitter/staging/
  README.md
  tweets.sanitized.jsonl
  review.csv
  media-map.json
  excluded-summary.json
  export-manifest.json
```

The staging folder is ignored by Git except for its README. Generated exports are for review before publication.

## Canonical Distinction

Twitter/X posts are public fragments and historical records. They are not treated as final doctrine, current thinking, or edited essays.

Published essays remain the canonical expression of developed ideas. The archive exists to preserve lineage.
