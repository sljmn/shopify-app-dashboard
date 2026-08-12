# Store-wide Review Intelligence Design

## Goal

Collect every public Shopify App Store review over time and turn the resulting history into a searchable review feed and category-relative growth signals. Large incumbents remain visible as benchmarks, but do not automatically dominate the default gem view.

## Collection Strategy

The existing parser and idempotent `discovery_reviews` store remain authoritative. Collection becomes a rotating, bounded queue across every active discovered app. Each run selects the least recently checked apps, reads the newest review page, and advances one historical page for apps whose backfill is incomplete. Running the bounded queue hourly gives every app repeated coverage without issuing roughly 25,000 requests in one burst. Followed apps and apps with recent category-review growth receive priority, but every active app remains eligible until its complete public review history is captured.

Review IDs remain the external identity. Re-reading a review updates mutable developer replies and metadata without creating duplicates. Failures are recorded per app and do not stop the queue. App-store category totals remain the source for total review counts; captured review rows are the source for review content and review publication timelines.

## Category-relative Gem Model

There is no global maximum review count. Each app is compared with active competitors in each category:

- recent review velocity over 7, 30, and 90 days;
- acceleration versus the preceding part of the selected period;
- review-count percentile within its category;
- velocity percentile within its category;
- category size, median reviews, top-ten review concentration, and active-grower share;
- rating and category-rank movement;
- data confidence from scan recency, captured history, and observation depth.

An established app is one in the highest review-count band of its own category. An unexpected grower is outside that incumbent band but in the leading category-relative velocity band. A rising gem combines relative velocity, acceleration, rank movement, quality, and category opportunity. The UI exposes the inputs and confidence so the score remains explainable.

Relative labels require at least five measured competitors. Smaller or uncategorized groups remain visible under All apps but do not claim a gem or unexpected-grower classification.

## Product Surface

`Discover / Reviews` provides two connected views:

1. A newest-first feed of captured reviews showing app, category, rating, merchant context, review text, reply state, and Shopify source.
2. An app-level intelligence table showing recent review counts, acceleration, category percentile, competitive context, gem score, and confidence.

Filters cover period, category, rating, price model, BFS status, and smart presets: all apps, rising gems, unexpected growers, and established benchmarks. App names link to the existing public-app detail page and individual reviews link to Shopify.

## Operations And Correctness

The scheduler uses a fixed per-run request budget and database ordering, making restarts and overlapping discoveries safe. Delisted apps are excluded from collection but retained in historical reports. Empty or failed Shopify responses never mark a backfill complete unless pagination explicitly ends. Tests cover queue fairness, priority, retry state, category-relative classification, score bounds, confidence, filtering, and responsive rendering.
