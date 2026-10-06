# Localhost decisions

- Keep real Flask authentication and SQLite in the demo and regression tests.
  Tests set `DATABASE_URL` to temporary files to protect developer data.
- Bind the starter server to loopback with debugging off by default.
- Generate a new secret only for a missing local environment file. Preserve
  existing configuration and data; fail on incompatible schemas, never drop.
- Preserve the inherited game envelope and account upsert API for compatibility
  with the frontend; do not expand starter work into a persistence redesign.
