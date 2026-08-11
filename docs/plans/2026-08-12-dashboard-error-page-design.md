# Dashboard Error Page Design

## Goal

Make signed-in browser errors feel like part of the analytics dashboard and keep
the retired Dutch activity URL useful.

## Design

Signed-in 401/403/404 responses keep the existing sidebar and render on the
normal light dashboard surface. The message sits in the standard constrained
content column with a small status label, a concise heading, supporting copy,
and existing button styling. It uses no illustration, cover image, overlay, or
special full-height composition.

Signed-out errors continue to use the unauthenticated gate because they cannot
show working navigation. The login screen is unaffected.

`/activiteit` permanently redirects to `/activity`, retaining the query string
so old filtered links remain useful. Other unknown URLs continue to return a
real 404 response.

## Verification

Request the old route and verify its redirect target, request an unknown route
with a signed-in session and verify the regular dashboard shell, and confirm a
signed-out 404 remains usable. Check desktop and mobile layouts and run the full
test suite.
