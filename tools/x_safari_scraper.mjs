#!/usr/bin/env node

import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

function parseArgs(argv) {
  const values = {
    username: "SayitSalty",
    cursor: "",
    cutoff: "",
    output: "",
    maxScrolls: 80,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--username") values.username = argv[++index];
    else if (value === "--cursor") values.cursor = argv[++index];
    else if (value === "--cutoff") values.cutoff = argv[++index];
    else if (value === "--output") values.output = argv[++index];
    else if (value === "--max-scrolls") values.maxScrolls = Number(argv[++index]);
    else throw new Error(`Unknown argument: ${value}`);
  }
  return values;
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function normalizeText(value) {
  return String(value || "").replace(/\u00a0/g, " ").trim();
}

function postKind({ socialContext, replyContext, statusLinks, username }) {
  if (/reposted/i.test(socialContext)) return "retweet";
  if (replyContext) {
    return replyContext.toLowerCase().includes(`@${username.toLowerCase()}`)
      ? "self_thread_reply"
      : "reply";
  }
  return statusLinks.length > 1 ? "quote" : "original";
}

function canonicalize(post, username) {
  const created = new Date(post.datetime);
  if (!post.id || Number.isNaN(created.getTime())) return null;

  const kind = postKind({
    socialContext: post.socialContext,
    replyContext: post.replyContext,
    statusLinks: post.statusLinks,
    username,
  });
  const quoted = post.statusLinks.find(
    (item) => item.id !== post.id && item.username.toLowerCase() !== username.toLowerCase(),
  );

  return {
    id: post.id,
    created_at: post.datetime,
    created_at_utc: created.toISOString(),
    year: String(created.getUTCFullYear()),
    kind,
    text: normalizeText(post.text),
    tweet_url: `https://x.com/${username}/status/${post.id}`,
    in_reply_to_status_id: null,
    in_reply_to_screen_name: post.replyContext || null,
    quoted_status_id: quoted?.id || null,
    urls: [],
    media: post.media,
    review_status: "pending",
    canonical_status: "archive_fragment_not_canon",
  };
}

function canonicalizeRepost(observation, username, observedAt) {
  const sourceCreated = new Date(observation.datetime);
  if (!observation.id || !observation.sourceUsername || Number.isNaN(sourceCreated.getTime())) {
    return null;
  }

  const recurrenceKey = [
    "x-repost",
    username.toLowerCase(),
    observation.sourceUsername.toLowerCase(),
    observation.id,
  ].join(":");
  const observationId = createHash("sha256").update(recurrenceKey).digest("hex");

  return {
    observation_id: observationId,
    observation_kind: "repost",
    recurrence_key: recurrenceKey,
    observed_at_utc: observedAt,
    source_post: {
      id: observation.id,
      author_username: observation.sourceUsername,
      created_at_utc: sourceCreated.toISOString(),
      text: normalizeText(observation.text),
      url: `https://x.com/${observation.sourceUsername}/status/${observation.id}`,
      media: observation.media,
    },
    self_repost: observation.sourceUsername.toLowerCase() === username.toLowerCase(),
    canonical_status: "provisional_profile_observation",
    provenance: {
      surface: "public_profile",
      exact_repost_event_id_available: false,
      exact_repost_timestamp_available: false,
      counting_note:
        "A repeated daily sighting is not counted as another repost action. " +
        "Canonical event counts require an official X archive.",
    },
  };
}

async function runAppleScript(script, args = []) {
  try {
    const { stdout } = await execFileAsync("osascript", ["-e", script, ...args], {
      maxBuffer: 16 * 1024 * 1024,
    });
    return stdout.trim();
  } catch (error) {
    const detail = String(error.stderr || error.message || error);
    if (detail.includes("Allow JavaScript from Apple Events")) {
      throw new Error(
        "Safari blocks local page automation. In Safari Settings, enable Developer > " +
          "Allow JavaScript from Apple Events, then run the scraper again.",
      );
    }
    throw new Error(`Safari automation failed: ${detail.trim()}`);
  }
}

async function prepareProfileTab(username) {
  const target = `https://x.com/${username}/with_replies`;
  const script = `
on run argv
  set accountNeedle to item 1 of argv
  set targetURL to item 2 of argv
  tell application "Safari"
    activate
    repeat with browserWindow in windows
      repeat with browserTab in tabs of browserWindow
        set tabURL to URL of browserTab
        if tabURL contains accountNeedle then
          set URL of browserTab to targetURL
          set current tab of browserWindow to browserTab
          set index of browserWindow to 1
          return targetURL
        end if
      end repeat
    end repeat
    if (count of windows) is 0 then make new document
    set URL of current tab of front window to targetURL
    return targetURL
  end tell
end run`;
  await runAppleScript(script, [`x.com/${username}`, target]);
}

async function safariEvaluate(username, javascript) {
  const script = `
on run argv
  set accountNeedle to item 1 of argv
  set javascriptSource to item 2 of argv
  tell application "Safari"
    repeat with browserWindow in windows
      repeat with browserTab in tabs of browserWindow
        set tabURL to URL of browserTab
        if tabURL contains accountNeedle then
          return do JavaScript javascriptSource in browserTab
        end if
      end repeat
    end repeat
  end tell
  error "No trusted Safari tab is open for " & accountNeedle
end run`;
  return runAppleScript(script, [`x.com/${username}`, javascript]);
}

function extractionScript(username) {
  return `(() => {
    const account = ${JSON.stringify(username)};
    const clean = (value) => String(value || "").replace(/\\u00a0/g, " ").trim();
    const ownPattern = new RegExp("/" + account + "/status/(\\\\d+)", "i");
    const statusPattern = /\\/([^/]+)\\/status\\/(\\d+)/i;
    const authoredPosts = [];
    const repostObservations = [];
    for (const article of document.querySelectorAll('article[data-testid="tweet"]')) {
      const anchors = [...article.querySelectorAll('a[href*="/status/"]')];
      const timedAnchor = anchors.find((anchor) => anchor.querySelector("time"));
      const socialNode = article.querySelector('[data-testid="socialContext"]');
      const socialContext = clean(socialNode?.innerText);
      const isRepost = /reposted/i.test(socialContext);
      const textNode = article.querySelector('[data-testid="tweetText"]');
      const articleText = clean(article.innerText);
      const media = [...article.querySelectorAll('[data-testid="tweetPhoto"] img, video[poster]')]
        .map((node) => ({
          type: node.tagName.toLowerCase() === "video" ? "video" : "photo",
          media_url: node.getAttribute("src") || node.getAttribute("poster"),
          alt_text: node.getAttribute("alt") || null,
        }));

      if (isRepost && timedAnchor) {
        const sourceMatch = (timedAnchor.getAttribute("href") || "").match(statusPattern);
        if (sourceMatch) {
          repostObservations.push({
            id: sourceMatch[2],
            sourceUsername: sourceMatch[1],
            datetime: timedAnchor.querySelector("time")?.getAttribute("datetime") || null,
            text: clean(textNode?.innerText),
            socialContext,
            media,
          });
        }
        continue;
      }

      const ownAnchor =
        anchors.find((anchor) => anchor.querySelector("time") && ownPattern.test(anchor.getAttribute("href") || "")) ||
        anchors.find((anchor) => ownPattern.test(anchor.getAttribute("href") || ""));
      if (!ownAnchor) continue;

      const ownMatch = (ownAnchor.getAttribute("href") || "").match(ownPattern);
      if (!ownMatch) continue;

      const statusLinks = anchors
        .map((anchor) => (anchor.getAttribute("href") || "").match(statusPattern))
        .filter(Boolean)
        .map((match) => ({ username: match[1], id: match[2] }))
        .filter((item, index, items) =>
          items.findIndex((candidate) => candidate.username === item.username && candidate.id === item.id) === index
        );
      const timeNode = ownAnchor.querySelector("time") || article.querySelector("time");
      const replyMatch = articleText.match(/Replying to\\s+([^\\n]+)/i);

      authoredPosts.push({
        id: ownMatch[1],
        datetime: timeNode?.getAttribute("datetime") || null,
        text: clean(textNode?.innerText),
        socialContext,
        pinned: /^Pinned(?:\\n|$)/i.test(articleText),
        replyContext: replyMatch ? clean(replyMatch[1]) : "",
        statusLinks,
        media,
      });
    }
    return JSON.stringify({
      ready: document.querySelectorAll('article[data-testid="tweet"]').length > 0,
      loginRequired: Boolean(document.querySelector('a[href="/login"]')),
      temporarilyLimited: /temporarily limited|rate limit|try again later/i.test(document.body.innerText),
      posts: authoredPosts,
      repostObservations,
    });
  })()`;
}

function expandTruncatedPostsScript() {
  return `(() => {
    const controls = [...document.querySelectorAll('[data-testid="tweet-text-show-more-link"]')];
    controls.forEach((control) => control.click());
    return String(controls.length);
  })()`;
}

async function scrape(options) {
  if (!options.cursor) throw new Error("--cursor is required.");
  if (!options.cutoff || Number.isNaN(new Date(options.cutoff).getTime())) {
    throw new Error("--cutoff must be a valid date.");
  }
  if (!options.output) throw new Error("--output is required.");

  await prepareProfileTab(options.username);
  await sleep(5000);

  const found = new Map();
  const foundReposts = new Map();
  const collectedAt = new Date().toISOString();
  const cutoffTime = new Date(options.cutoff).getTime();
  let cursorSeen = false;
  let cutoffReached = false;
  let unchangedRounds = 0;
  let previousSize = 0;
  let scrolls = 0;

  for (; scrolls < options.maxScrolls; scrolls += 1) {
    const expanded = Number(
      await safariEvaluate(options.username, expandTruncatedPostsScript()),
    );
    if (expanded > 0) await sleep(500);

    const raw = await safariEvaluate(options.username, extractionScript(options.username));
    const observation = JSON.parse(raw || "{}");
    if (observation.loginRequired && !observation.ready) {
      throw new Error(
        "The trusted Safari tab is not signed in to X. Do not retry while X has a temporary login limit.",
      );
    }
    if (observation.temporarilyLimited && !observation.ready) {
      throw new Error(
        "X is temporarily limiting this Safari session. No review bundle was written; wait before retrying.",
      );
    }
    for (const post of observation.posts || []) {
      found.set(post.id, post);
      if (post.id === options.cursor) cursorSeen = true;
    }
    for (const repost of observation.repostObservations || []) {
      const key = `${repost.sourceUsername.toLowerCase()}:${repost.id}`;
      foundReposts.set(key, repost);
    }
    const boundaryPosts = [...found.values()].filter((post) => {
      const timestamp = new Date(post.datetime).getTime();
      return (
        !post.pinned &&
        !/reposted/i.test(post.socialContext) &&
        BigInt(post.id) <= BigInt(options.cursor) &&
        Number.isFinite(timestamp) &&
        timestamp <= cutoffTime
      );
    });
    // Ignore pinned and reposted cards because they can surface old IDs at the
    // top. Multiple ordinary authored IDs prove traversal crossed the boundary.
    cutoffReached = boundaryPosts.length >= 3;
    if (cursorSeen || cutoffReached) break;

    const observedSize = found.size + foundReposts.size;
    unchangedRounds = observedSize === previousSize ? unchangedRounds + 1 : 0;
    previousSize = observedSize;
    if (unchangedRounds >= 5) break;

    await safariEvaluate(
      options.username,
      "window.scrollBy(0, Math.max(window.innerHeight * 0.85, 800)); 'ok'",
    );
    await sleep(1300);
  }

  if (!cursorSeen && !cutoffReached) {
    throw new Error(
      `The scraper stopped after ${scrolls} scrolls without crossing the saved archive boundary. ` +
        "No partial review bundle was written. Retry later or increase --max-scrolls.",
    );
  }

  const records = [...found.values()]
    .filter((post) => BigInt(post.id) > BigInt(options.cursor))
    .map((post) => canonicalize(post, options.username))
    .filter(Boolean)
    .sort((left, right) => left.created_at_utc.localeCompare(right.created_at_utc));
  const repostObservations = [...foundReposts.values()]
    .map((post) => canonicalizeRepost(post, options.username, collectedAt))
    .filter(Boolean)
    .sort((left, right) =>
      left.source_post.created_at_utc.localeCompare(right.source_post.created_at_utc),
    );

  const payload = {
    schema_version: 2,
    source: {
      adapter: "trusted_safari_public_profile_scraper",
      account_username: options.username,
      collected_at_utc: collectedAt,
      since_id: options.cursor,
      cutoff_at_utc: new Date(cutoffTime).toISOString(),
      cursor_seen: cursorSeen,
      cutoff_reached: cutoffReached,
      scrolls,
      public_posts_observed: found.size,
      repost_observations: repostObservations.length,
      self_repost_observations: repostObservations.filter((item) => item.self_repost).length,
      repost_event_identity: "unavailable_on_public_profile",
      private_surfaces_requested: false,
      cookies_exported: false,
    },
    posts: records,
    repost_observations: repostObservations,
  };
  await fs.mkdir(path.dirname(path.resolve(options.output)), { recursive: true });
  await fs.writeFile(options.output, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  process.stdout.write(`${JSON.stringify(payload.source, null, 2)}\n`);
}

const options = parseArgs(process.argv.slice(2));
await scrape(options);
