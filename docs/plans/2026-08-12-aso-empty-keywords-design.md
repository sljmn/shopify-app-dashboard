# Empty ASO Keywords Design

## Problem

GA4 exposes the `searchTerm` dimension in property metadata, but Shopify's
events currently provide an empty value for it. Mantle stores those rows,
counts the empty value as one observed keyword, and renders a blank table row.
The resulting users and clicks describe general channel traffic, not observed
keywords.

## Design

Only rows with a non-empty normalized keyword belong in
`aso_keyword_daily`. The importer drops empty and whitespace-only terms, and
the reporting queries retain the same boundary so previously stored bad rows
cannot leak into the UI. After an import that returns no usable keyword rows,
the keyword capability is stored as `unsupported` with the explicit error code
`NoKeywordValues`.

The ASO page renders a specific empty state explaining that GA4 supplied no
search terms. General GA4 channel traffic remains available on Traffic and is
not relabelled as keyword traffic.

## Verification

Regression tests cover importer filtering, persisted capability status, report
filtering, and the page empty state. The complete test suite and a production
refresh verify that the blank keyword and inflated totals disappear.
