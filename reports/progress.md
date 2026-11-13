# Localhost foundation

## 2026-10-06

- Committed `run_backend.sh`, `.env.example`, environment/database overrides,
  health check, loopback defaults, and tests. Removed JWT console logging.
- Replaced automatic deletion of incompatible legacy games tables with an
  explicit error that preserves existing rows.
- Three Flask/SQLite checks passed. Both servers started from their scripts;
  browser password login, account save, reload, load, and playback passed with
  the integrated GameMaker-CSP frontend. Updated README startup instructions.
- Remaining capstone features and production/cloud verification are separate
  work; this checkpoint completes the local foundation pipeline.

## 2026-11-12: UESL content through OCS persona API

Added data/gamemaker_personas.json and model/gamemaker_personas.py. Startup inserts
missing gm- aliases only; old OCS rows and selections are not relabeled or deleted.
GET /api/persona?collection=gamemaker returns the UESL catalogue; the existing
user/persona and user/personas endpoints handle selections. Seven isolated tests
passed, including persistence and account isolation. Local browser verified all
four selections through the OCS frontend. No production changes or commits.

## 2026-11-13: prepare main checkpoint

The seven isolated Flask/SQLite and deployment-contract tests passed again.
They cover authenticated profile updates, preference persistence after login,
account isolation, cookies, and disabled code runners in deployment mode.
Pending account catalogue, startup output, Docker/proxy preparation and tests
are being committed to main at the user's request. No secrets, databases or
runtime files are included; production deployment remains paused.
