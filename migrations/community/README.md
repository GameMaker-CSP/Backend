# Event contract for team review

A series links gatherings over time. Each occurrence has its own permanent ID, local display timezone, UTC start and end, venue, status, photos and discussion. Staff enter each occurrence. The homepage lists the next two published occurrences in start-time order. Past occurrences keep their existing URLs. Game Maker remains available between gatherings.

```mermaid
flowchart LR
  Series --> Occurrence
  Occurrence --> Photos
  Occurrence --> Discussion
  Account --> RSVP
  RSVP --> Occurrence
  Staff --> Attendance
  Attendance --> Occurrence
```

### Community API

The identifiers below are examples. No real event or attendee is seeded.

| Request | Response or effect |
| --- | --- |
| GET /api/community/events?from=2030-01-01T00:00:00Z | Up to 50 overlapping published or cancelled occurrences ordered by UTC start and ID. Use page=2 for the next page |
| POST /api/community/events | Staff supply title, type, location, timezone and dates. Returns a draft and its ID |
| PATCH /api/community/events/12 | Staff update the occurrence or set its state to published or cancelled |
| GET /api/community/events/12 | Event, series ID and nullable cover photo ID. An unpublished occurrence is hidden from visitors |
| POST /api/community/events/12/photos | Staff send image, alt text, caption and permission/credit. Returns photo ID belonging to 12 |
| POST /api/community/events/12/comments | Signed-in member sends text and optional parent ID. Server assigns author and time |
| PUT /api/community/events/12/rsvp | Send a boolean attending value to set or cancel the current account's RSVP. Repeating the request has the same effect |
| PUT /api/community/events/12/roster/34 | Staff record actual attendance independently of RSVP |

PATCH /api/community/photos/ID with cover=true selects one cover per occurrence. Each upload requires permission confirmation, credit, alt text and a caption. The database stores re-encoded JPEG bytes with the event. Failed validation commits no image. A database backup includes the photos. Account settings remain in the existing OCS persona collection. An RSVP records intent; attendance records staff check-in. Keep attendance private to the account and authorized staff. Confirm retention with UESL before collecting real records.

Review with the feature owners before expanding the schema: confirm event types, who can publish and check in attendees, and whether cancelled events remain publicly visible. The original SQLite migration describes the first event and series tables. Flask initializes the additional tables on startup. Back up the database before deploying schema changes. To roll back, stop the service and restore the previous database backup and application revision together.

## Event albums and discussion

Both APIs use the same published events, descriptions, covers, photos and comments. Album uploads add private permission notes and thumbnails in the `uesl_album_photo_info` table.

The `/api/events` endpoints reuse the normal OCS session and configured staff
roles. The staff endpoints list drafts (`GET /manage`), accept multipart photo
uploads (`POST /<id>/photos`), and save a complete order and cover together
(`PUT /<id>/photos`). The public album index (`GET /api/events?albums=true`)
includes each album's selected `cover` metadata, or `null` if unset. Private permission references and draft images are
available only to staff. The frontend uses `X-Origin: client` and the existing
allowed-origin policy for mutations. See the route tests for examples.

The app decodes still JPEG, PNG and WebP images (up to 4 MiB and 24 megapixels),
strips metadata, and transactionally stores normalized JPEG display and
thumbnail bytes in the same database as the album. The original names and
files are never retained. Invalid uploads leave no files or rows. Removing an
image deletes its bytes and clears its cover selection if needed. Back up the
configured database, including BLOBs (with the existing persistent `instance`
volume for SQLite), and include it in restore tests. Backups may temporarily
retain removed content; follow UESL’s agreed retention and access policy.
The active HTTPS proxy needs to accept 5 MiB for multipart overhead; the
checked-in `deploy/ueslgame-api-location.conf` sets this for API requests.

Event discussion reads are paginated (`after=<last ID>`), and replies are
limited to one level. Writes derive the author from the active account.
Removal clears the text but keeps a tombstone so replies stay in context.
Only the author may edit; both authors and staff may remove. This is scoped
to the event and does not use chat or a WebSocket.
