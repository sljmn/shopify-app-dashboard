# Discovery Research Workspace

## Goal

Extend Discover with named research lists, searchable notes and attachments for
lists, public apps, and Shopify developers. A developer page shows every indexed
app from that developer. Research attachments live in a private Backblaze B2
bucket instead of the Dokku filesystem.

## Existing foundation

The existing discovery warehouse remains authoritative:

- `discovered_apps` is the store-wide catalog keyed by Shopify handle.
- `discovery_watchlist` controls listing, review, rank, and media collection.
- `discovery_listing_snapshots` already stores immutable listing versions.
- `parse_listing()` already captures the developer name and stable partner URL.
- Discover already provides catalog search, category inventory, growth signals,
  app details, and a technical watchlist.

Research is a human organization layer on these tables, not a replacement for
the technical watchlist.

## User experience

### Research index

`/research` is a table-first workspace with segments for All, Lists, Notes, and
Attachments. It sorts by most recently updated and shows date, title, type,
context, related list, attachment count or type, and author. Search covers list
titles and descriptions, note titles and bodies, app names and handles,
developer names, and attachment filenames. Filters cover type, list, status,
and date.

### Lists

A list has a title, description, active or archived status, and timestamps. The
same app may appear in multiple lists. A list detail page contains its apps,
notes, attachments, and recent activity. Adding an app to a list also calls the
existing idempotent `follow_app(..., source="manual")` path so tracking starts.
Removing it from a list never disables tracking automatically.

### Notes and attachments

A note has a title and body and targets exactly one list, discovered app, or
developer. Notes support multiple attachments: JPEG, PNG, WebP, GIF, PDF, DOCX,
XLSX, PPTX, CSV, or plain text. SVG, HTML, scripts, and executable formats are
rejected. The limit is 15 MB per file. The server validates size, extension,
declared MIME type, and file signature before accepting an upload.

### Apps and developers

Discover rows and app detail pages get Add to list and New note actions. The app
detail page also shows its lists and notes. Where developer data is available,
the developer name links to `/research/developers/{id}`.

A developer is keyed by its normalized public Shopify partner URL. When a
followed or researched app is scanned, Mantle upserts the developer relationship.
Mantle then reads that developer page, discovers every linked App Store handle,
and stores the complete app/developer mapping. Researched developers refresh on
a daily GoodJob-equivalent scheduler run.

## Data model

The migration adds:

- `research_lists`: title, description, status, created/updated timestamps.
- `research_list_apps`: list/app membership with `added_at` and a unique pair.
- `discovered_developers`: display name, unique normalized Shopify URL,
  `last_scanned_at`, and timestamps.
- `discovered_app_developers`: unique developer/app pairs.
- `research_notes`: title, body, one nullable foreign key for list/app/developer,
  author label, and timestamps. A database constraint enforces one target.
- `research_attachment_objects`: SHA-256 digest, object key, MIME type, byte
  size, original filename, and creation timestamp.
- `research_note_attachments`: ordered note/object relationships.

Deleting a list cascades its membership and list notes, but never deletes public
apps, developers, watch state, or listing history. Attachment objects are
content-addressed and may be shared by identical uploads. An object is removed
from B2 only after its last database reference disappears.

## Backblaze B2 storage

Production uses a new private bucket and a bucket-scoped application key through
Backblaze's S3-compatible API. Configuration stays in Dokku:

- `B2_KEY_ID`
- `B2_APPLICATION_KEY`
- `B2_REGION`
- `B2_BUCKET`
- `B2_ENDPOINT`

The app uses `boto3`. Object keys are
`research/{digest[0:2]}/{digest}`. The SHA-256 digest is calculated while a
bounded temporary upload is validated, so duplicate files reuse one object.
The bucket is private. Authenticated download requests receive a short-lived
presigned URL. Secrets are neither committed nor rendered in Management.

Tests use an in-memory fake object store and do not require Backblaze. Local
development may use the same fake or explicit B2 configuration. Existing public
listing screenshot archives remain on the current persistent volume in this
increment; only human research attachments move to B2.

## Application boundaries

- `research.py` owns list, membership, note, attachment metadata, index queries,
  and target validation.
- `object_storage.py` owns content validation, hashing, B2 upload, presigning,
  and deletion. SQL code never talks to B2 directly.
- `developer_catalog.py` normalizes developer URLs, parses partner pages, and
  persists developer/app relationships.
- `web.py` owns authenticated forms, CSRF/origin validation, request size
  handling, redirects, and template contexts.
- Dedicated templates render the Research index, list detail/form, note form,
  and developer detail. Discover/app templates only expose entry points and
  concise research summaries.

## Failure handling

- Membership insertion is idempotent through its unique constraint.
- Research text is committed independently from external scraping. A failed
  listing or developer scan never loses a list or note.
- Failed B2 uploads show an actionable form error and create no attachment row.
- Invalid and oversized files are rejected before upload.
- A B2 object uploaded just before a database failure is deleted on compensation.
- Missing developer markup leaves the app usable without a developer relation.
- Scheduled developer refreshes record their last attempt/error without erasing
  previously known apps.

## Verification

Tests cover migrations and constraints, list CRUD, multi-list membership,
idempotent following, exactly-one note target, search/date ordering, developer
normalization and parsing, attachment validation and deduplication, B2 calls,
authentication, CSRF, and mobile layouts. The final deployment check uploads,
downloads, and deletes a small private test file, then verifies that the same
object cannot be fetched without an authenticated Mantle request.
