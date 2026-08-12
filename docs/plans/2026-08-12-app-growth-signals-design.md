# App Growth Signals Design

## Goal

Extend Discover with explainable store-wide signals for young rising apps, fastest review growers, and newly discovered contenders.

## Source and cadence

The existing twice-weekly category crawl already receives each app card's review count, rating, and ordered position. Capture those fields during the same requests; do not add one request per listing. Each completed crawl writes one dated app observation and one dated observation per app/category.

The first successful metrics crawl is a baseline. Growth appears only after a later observation, so missing history is shown as unknown rather than zero.

## Metrics

- Current reviews and rating come from the latest completed crawl.
- Seven- and thirty-day review growth compare the current observation with the latest observation at or before the respective cutoff. If no qualifying observation exists, the delta is unavailable.
- Relative growth is shown only when the comparison count is positive.
- Best category rank is the lowest current position across all categories. Rank movement compares the same category with its previous observation; positive means moving toward position one.
- App age is the time since Mantle first observed the handle. Baseline apps have unknown true age and are never described as newly launched.

## Lists

1. **Rising gems:** non-baseline apps first observed within 180 days, 5–250 current reviews, at least 3 new reviews in the measured window, ranked by a transparent combination of review velocity, relative growth, and upward category movement.
2. **Fastest growers:** all apps with positive measured review growth, sorted by review velocity normalized to 30 days and then current review count.
3. **New contenders:** non-baseline apps first observed within 60 days, sorted by review growth, current reviews, and best category rank.

Every row exposes the component metrics. No list claims installs, revenue, or publication date: those are not public App Store data.

## Reliability

One app is counted once in each list even if it appears in multiple categories. Its maximum consistent card review count and best rank are used for the app-level observation. A failed or empty crawl writes no observations and preserves the prior successful state.

## Interface

Add a `Growth signals` section above the existing discovery filters with three compact tabs. Desktop tables show reviews, 7/30-day gains, rating, best rank and movement, age, and categories. Mobile retains the existing horizontal overflow instead of compressing labels into unreadable columns.
