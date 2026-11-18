BEGIN;
CREATE TABLE uesl_series (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL CHECK(length(trim(title)) > 0)
);
CREATE TABLE uesl_event (
    id INTEGER PRIMARY KEY,
    series_id INTEGER REFERENCES uesl_series(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL CHECK(length(trim(title)) > 0),
    location TEXT NOT NULL,
    timezone TEXT NOT NULL,
    starts_at_utc TEXT NOT NULL,
    ends_at_utc TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','published','cancelled')),
    CHECK(ends_at_utc > starts_at_utc)
);
CREATE INDEX uesl_event_calendar ON uesl_event(state, starts_at_utc, id);
COMMIT;
