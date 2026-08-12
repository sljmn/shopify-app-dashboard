# Research workspace

Research turns public App Store discovery into durable internal work. It keeps named lists, dated
notes, attachments, app relationships, and Shopify developer portfolios in one searchable index.

## Workflow

- Open **Discover** to search the indexed Shopify App Store catalog.
- Add an app to one or more lists, or create a note directly from the app's **Research** tab.
- Use **Research** for the complete index. Search covers note titles and bodies, list names, apps,
  developers, and attachment filenames. The table shows the date and title for each result.
- Open a developer from an app or note to see every indexed app published by that developer.
- Developer portfolios connected to research are refreshed daily at 05:45 Europe/Amsterdam. A
  failure for one developer does not stop the remaining refreshes.

Removing an app from a list only removes that membership. It does not delete the app, its public
listing history, direct notes, or other list memberships. Archiving a list keeps its research and
attachments available through the status filter.

## Private attachments

Attachments use a dedicated private Backblaze B2 bucket through its S3-compatible API. PostgreSQL
stores the filename and object metadata; B2 stores the bytes. The application calculates SHA-256
while validating an upload and stores it at `research/<first-two-digest-characters>/<digest>`.
Uploading identical content again reuses the existing object.

Downloads never expose permanent credentials or a public bucket URL. The authenticated attachment
route returns a signed URL that expires after 60 seconds.

Supported formats are JPEG, PNG, WebP, GIF, PDF, DOCX, XLSX, PPTX, CSV, and plain text. The default
maximum is 15 MiB per file. Both the declared MIME type and the file signature are checked.

Configure all five variables together; partial configuration prevents startup:

```dotenv
B2_KEY_ID=<bucket-scoped-key-id>
B2_APPLICATION_KEY=<bucket-scoped-application-key>
B2_REGION=eu-central-003
B2_BUCKET=<private-bucket-name>
B2_ENDPOINT=https://s3.eu-central-003.backblazeb2.com
RESEARCH_UPLOAD_MAX_BYTES=15728640
```

The key should be scoped to this bucket with read/write/delete/list access. The bucket must remain
private. Secrets belong in Dokku config or `.env`, never in `config/apps.yml`, the database, notes,
or Git.

## Operations

Apply migrations before starting a new release:

```bash
uv run python -m app_dashboard.migrate
```

The normal scheduler performs developer refreshes. To verify storage independently, upload a small
PNG and PDF to a test note, find both filenames through `/research`, download them while logged in,
then remove the note. Deleting the final reference also removes the underlying object; shared
content remains until its final attachment reference is removed.

If storage is unavailable, creating text research still succeeds. Attachment errors are shown on
the form and do not discard the note text.
