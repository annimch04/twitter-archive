from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from twitter_sync import (  # noqa: E402
    PrivacyBoundaryError,
    XApiError,
    collect_x_posts,
    load_input,
    publish_review_bundle,
    self_repost_meta,
    stage,
    stage_repost_observations,
    write_review_bundle,
)
from twitter_archive_export import resolve_self_reposts  # noqa: E402


def public_record(post_id: str, created_at: str = "Wed Jul 22 04:00:00 +0000 2026") -> dict:
    return {
        "tweet": {
            "id_str": post_id,
            "created_at": created_at,
            "full_text": "A public test fragment.",
            "entities": {"urls": []},
        }
    }


def repost_observation(
    source_post_id: str = "200",
    observed_at: str = "2026-07-25T16:00:00+00:00",
) -> dict:
    return {
        "observation_id": f"x-repost-sighting-{source_post_id}",
        "observation_kind": "repost",
        "recurrence_key": f"x:SayitSalty:source:{source_post_id}",
        "observed_at_utc": observed_at,
        "source_post": {
            "id": source_post_id,
            "author_username": "SayitSalty",
            "created_at_utc": "2026-07-24T12:00:00+00:00",
            "text": "An idea worth bringing forward again.",
            "tweet_url": f"https://x.com/SayitSalty/status/{source_post_id}",
            "media": [],
        },
        "self_repost": True,
        "canonical_status": "provisional_profile_observation",
        "provenance": {
            "adapter": "trusted_safari_public_profile_scraper",
            "event_identity": "unavailable_on_public_profile",
            "event_timestamp": "unavailable_on_public_profile",
            "counting_rule": "Repeat sightings do not establish additional repost actions.",
        },
    }


class TwitterSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = [
            {
                "id": "100",
                "created_at": "Wed Jul 22 03:00:00 +0000 2026",
                "created_at_utc": "2026-07-22T03:00:00+00:00",
                "year": "2026",
                "kind": "original",
                "text": "Already archived.",
                "tweet_url": "https://x.com/SayitSalty/status/100",
                "in_reply_to_status_id": None,
                "in_reply_to_screen_name": None,
                "quoted_status_id": None,
                "urls": [],
                "media": [],
                "review_status": "pending",
                "canonical_status": "archive_fragment_not_canon",
            }
        ]

    def test_duplicate_is_skipped_and_new_post_is_staged(self) -> None:
        rows = [public_record("100"), public_record("101")]
        new_records, report = stage(rows, self.baseline, "SayitSalty")
        self.assertEqual([row["id"] for row in new_records], ["101"])
        self.assertEqual(report["duplicate_records"], 1)
        self.assertEqual(report["new_public_records"], 1)

    def test_public_scraper_iso_timestamp_with_z_is_accepted(self) -> None:
        row = dict(self.baseline[0])
        row.update(
            {
                "id": "101",
                "created_at": "2026-07-25T16:28:38.000Z",
                "created_at_utc": "2026-07-25T16:28:38.000Z",
            }
        )
        new_records, report = stage([row], self.baseline, "SayitSalty")
        self.assertEqual([record["id"] for record in new_records], ["101"])
        self.assertEqual(report["new_public_records"], 1)

    def test_forbidden_direct_message_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "direct-messages.js"
            path.write_text("window.YTD.direct_messages.part0 = [];", encoding="utf-8")
            with self.assertRaises(PrivacyBoundaryError):
                load_input(path)

    def test_private_message_record_shape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "public-batch.json"
            path.write_text(json.dumps([{"dmConversation": {"messages": []}}]), encoding="utf-8")
            with self.assertRaises(PrivacyBoundaryError):
                load_input(path)

    def test_missing_public_post_identity_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            stage([{"tweet": {"full_text": "Missing identity."}}], self.baseline, "SayitSalty")

    def test_review_bundle_does_not_mutate_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline_path = root / "baseline.jsonl"
            baseline_path.write_text(json.dumps(self.baseline[0]) + "\n", encoding="utf-8")
            before = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
            records, report = stage([public_record("101")], self.baseline, "SayitSalty")
            output = root / "review"
            observations = [repost_observation()]
            write_review_bundle(output, records, observations, report, {"adapter": "test"})
            after = hashlib.sha256(baseline_path.read_bytes()).hexdigest()

            self.assertEqual(before, after)
            self.assertTrue((output / "new-posts.jsonl").exists())
            self.assertTrue((output / "repost-observations.jsonl").exists())
            self.assertTrue((output / "repost-review.csv").exists())
            self.assertTrue((output / "review.csv").exists())
            self.assertTrue((output / "sync-report.json").exists())

    def test_public_batch_loads_repost_observations_separately(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "public-batch.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "source": {"adapter": "test"},
                        "posts": [public_record("101")],
                        "repost_observations": [repost_observation()],
                    }
                ),
                encoding="utf-8",
            )
            posts, observations, metadata = load_input(path)

        self.assertEqual(len(posts), 1)
        self.assertEqual(len(observations), 1)
        self.assertEqual(metadata["collector"]["adapter"], "test")

    def test_approved_bundle_publishes_and_can_resume_without_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staging = root / "archive" / "twitter" / "staging"
            sync = root / "archive" / "twitter" / "sync"
            staging.mkdir(parents=True)
            sync.mkdir(parents=True)
            baseline_path = staging / "tweets.sanitized.jsonl"
            baseline_path.write_text(
                json.dumps(self.baseline[0]) + "\n",
                encoding="utf-8",
            )
            manifest_path = staging / "export-manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "repost_meta": {"canonical_repost_events": 7},
                        "canonical_note": "Public posts are archival fragments.",
                    }
                ),
                encoding="utf-8",
            )
            media_map_path = staging / "media-map.json"
            media_map_path.write_text("[]\n", encoding="utf-8")
            state_path = sync / "state.json"
            state_path.write_text("{}\n", encoding="utf-8")
            observations_path = sync / "repost-observations.jsonl"

            record = dict(self.baseline[0])
            record.update(
                {
                    "id": "101",
                    "created_at": "2026-07-23T12:00:00.000Z",
                    "created_at_utc": "2026-07-23T12:00:00.000Z",
                    "text": "A newly approved public fragment.",
                }
            )
            bundle = root / "review"
            report = {
                "schema_version": 1,
                "mode": "dry_run_review_only",
                "account_username": "SayitSalty",
                "new_public_records": 1,
                "source": {
                    "collector": {
                        "adapter": "test_public_profile",
                        "collected_at_utc": "2026-07-24T00:00:00Z",
                    }
                },
            }
            write_review_bundle(
                bundle,
                [record],
                [repost_observation()],
                report,
                report["source"],
            )

            first = publish_review_bundle(
                bundle,
                baseline_path,
                manifest_path,
                media_map_path,
                state_path,
                observations_path,
            )
            second = publish_review_bundle(
                bundle,
                baseline_path,
                manifest_path,
                media_map_path,
                state_path,
                observations_path,
            )

            published = [
                json.loads(line)
                for line in baseline_path.read_text(encoding="utf-8").splitlines()
            ]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(first["newly_added_posts"], 1)
            self.assertEqual(second["newly_added_posts"], 0)
            self.assertEqual(len(published), 2)
            self.assertEqual(manifest["active_tweets"], 2)
            self.assertEqual(manifest["repost_meta"]["canonical_repost_events"], 7)
            self.assertTrue((root / "archive" / "twitter" / "2026" / "README.md").exists())
            self.assertEqual(len(observations_path.read_text().splitlines()), 1)

    def test_repeated_repost_sighting_does_not_create_another_event(self) -> None:
        first = repost_observation()
        repeated = repost_observation(observed_at="2026-07-26T16:00:00+00:00")
        staged, report = stage_repost_observations([first, repeated])

        self.assertEqual(len(staged), 1)
        self.assertEqual(report["duplicate_repost_observations"], 1)
        self.assertEqual(report["provisional_self_repost_source_count"], 1)

    def test_self_repost_meta_keeps_canonical_events_separate_from_sightings(self) -> None:
        baseline = self.baseline + [
            {
                **self.baseline[0],
                "id": "99",
                "kind": "retweet",
                "text": "RT @SayitSalty: An idea returning through the archive.",
            }
        ]
        staged, _ = stage_repost_observations(
            [
                repost_observation(),
                repost_observation(observed_at="2026-07-26T16:00:00+00:00"),
            ]
        )
        meta = self_repost_meta(baseline, staged, "SayitSalty")

        self.assertEqual(meta["canonical_event_count_in_official_archive"], 1)
        self.assertEqual(meta["provisional_distinct_source_count_in_batch"], 1)
        self.assertIn("recursive thinking", meta["interpretation"])

    def test_official_self_repost_resolves_to_original_for_per_post_count(self) -> None:
        tweets = [
            {
                "id_str": "100",
                "full_text": "An idea worth bringing forward again.",
            },
            {
                "id_str": "200",
                "full_text": "RT @SayitSalty: An idea worth bringing forward again.",
            },
        ]

        by_source, by_event, counts = resolve_self_reposts(tweets, "SayitSalty")

        self.assertEqual(by_source, {"100": ["200"]})
        self.assertEqual(by_event["200"]["source_post_id"], "100")
        self.assertEqual(by_event["200"]["source_match"], "exact")
        self.assertEqual(counts["resolved"], 1)

    def test_ambiguous_truncated_self_repost_is_not_assigned(self) -> None:
        tweets = [
            {
                "id_str": "100",
                "full_text": "The same long opening text continues one way.",
            },
            {
                "id_str": "101",
                "full_text": "The same long opening text continues another way.",
            },
            {
                "id_str": "200",
                "full_text": "RT @SayitSalty: The same long opening text…",
            },
        ]

        by_source, by_event, counts = resolve_self_reposts(tweets, "SayitSalty")

        self.assertEqual(by_source, {})
        self.assertIsNone(by_event["200"]["source_post_id"])
        self.assertEqual(
            by_event["200"]["source_match"],
            "ambiguous_truncated_prefix",
        )
        self.assertEqual(counts["ambiguous"], 1)

    def test_official_collector_uses_baseline_cutoff_and_normalizes_posts(self) -> None:
        requests = []

        def fake_request(path, params, token, api_base):
            requests.append((path, params, token, api_base))
            if path == "users/by/username/SayitSalty":
                return {"data": {"id": "42", "username": "SayitSalty"}}
            return {
                "data": [
                    {
                        "id": "101",
                        "text": "New public post.",
                        "created_at": "2026-07-23T12:00:00.000Z",
                        "attachments": {"media_keys": ["3_101"]},
                    },
                    {
                        "id": "102",
                        "text": "A reply.",
                        "created_at": "2026-07-23T12:05:00.000Z",
                        "in_reply_to_user_id": "99",
                        "referenced_tweets": [{"type": "replied_to", "id": "88"}],
                    },
                ],
                "includes": {
                    "media": [
                        {
                            "media_key": "3_101",
                            "type": "photo",
                            "url": "https://pbs.twimg.com/media/example.jpg",
                        }
                    ]
                },
                "meta": {"result_count": 2},
            }

        records, source = collect_x_posts(
            "SayitSalty",
            "100",
            "secret-token",
            request_json=fake_request,
        )

        timeline_request = requests[1]
        self.assertEqual(timeline_request[0], "users/42/tweets")
        self.assertEqual(timeline_request[1]["since_id"], "100")
        self.assertEqual([record["id"] for record in records], ["101", "102"])
        self.assertEqual(records[0]["kind"], "original")
        self.assertEqual(records[0]["media"][0]["type"], "photo")
        self.assertEqual(records[1]["kind"], "reply")
        self.assertEqual(source["private_sources_requested"], False)

    def test_official_collector_paginates(self) -> None:
        calls = []

        def fake_request(path, params, token, api_base):
            calls.append(dict(params))
            post_id = "101" if len(calls) == 1 else "102"
            payload = {
                "data": [
                    {
                        "id": post_id,
                        "text": f"Post {post_id}",
                        "created_at": "2026-07-23T12:00:00.000Z",
                    }
                ],
                "meta": {},
            }
            if len(calls) == 1:
                payload["meta"]["next_token"] = "next-page"
            return payload

        records, source = collect_x_posts(
            "SayitSalty",
            "100",
            "secret-token",
            user_id="42",
            request_json=fake_request,
        )
        self.assertEqual([record["id"] for record in records], ["101", "102"])
        self.assertIsNone(calls[0]["pagination_token"])
        self.assertEqual(calls[1]["pagination_token"], "next-page")
        self.assertEqual(source["pages_read"], 2)

    def test_collector_error_does_not_expose_token(self) -> None:
        def failing_request(path, params, token, api_base):
            raise XApiError("Credential rejected.")

        with self.assertRaisesRegex(XApiError, "Credential rejected"):
            collect_x_posts(
                "SayitSalty",
                "100",
                "do-not-print-me",
                user_id="42",
                request_json=failing_request,
            )


if __name__ == "__main__":
    unittest.main()
