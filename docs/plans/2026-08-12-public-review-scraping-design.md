# Public Review Scraping Design

## Goal

Capture complete public Shopify App Store reviews for actively followed apps and newly discovered apps during their one-time enrichment, without crawling the complete App Store or changing existing review-growth metrics.

## Source And Scope

The collector reads `https://apps.shopify.com/{handle}/reviews?sort_by=newest&page=N`. Shopify review IDs are the stable external identity. Each stored review includes rating, publication date, merchant display name, country, usage duration, body, and the developer reply text/date when present.

Active watchlist apps are checked daily. A newly discovered app is not removed from its one-time enrichment queue until both its listing and first review page have been processed. Existing followed apps receive a historical backfill in bounded batches. Daily incremental runs start at page one and stop when a page contains only known reviews. Historical backfill resumes from its persisted next page and marks completion when Shopify returns no reviews or no next page.

## Data Model

`discovery_reviews` stores one row per `(discovered_app_id, shopify_review_id)` and updates mutable fields such as developer replies if Shopify changes them. `discovery_review_sync_state` stores the last attempt, last success, error code, next historical page, and completion timestamp per app. Review IDs make retries and overlapping daily jobs idempotent.

## Product Surface

The public-app detail page gains a `Reviews` tab. It shows backfill state, total captured reviews, rating distribution, and reviews newest-first with rating, date, merchant metadata, body, and developer reply. A star filter changes only the stored review query.

Review-count growth and category opportunity calculations continue to use `discovery_app_observations`, because category pages provide the complete public total. Captured review rows are supporting qualitative evidence and their partial backfill must never masquerade as a complete review count.

## Operations And Safety

The existing daily watchlist job calls the review collector after each listing scan. Requests use the current retry helper, user agent, bounded page count, and a short delay. One app failure is recorded and isolated from other apps. Tests cover parsing, pagination stop conditions, idempotent upserts, reply updates, backfill continuation, and the Reviews UI. Production deployment is followed by an explicit bounded backfill run for current watched apps.
