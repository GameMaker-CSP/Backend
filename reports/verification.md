# Verification — 2026-10-06

- `bash run_backend.sh`: started on `127.0.0.1:8424` using `.venv`.
- `.venv/bin/python -m unittest discover -s tests -p 'test_local_pipeline.py'`:
  all three tests passed using temporary databases. Covered health, allowed
  and denied CORS origins, password login, account isolation, save/load/upsert,
  deletion, logout, and legacy-schema data preservation.
- Browser on `localhost:4700`: demo account login succeeded; `Merged Team Demo`
  saved, appeared in My Games after reload, loaded with its title/background/
  levels preserved, and played. Browser console had no errors.
- Inherited `datetime.utcnow()` warnings and missing optional KASM-key notices
  were observed; they did not prevent the tested pipeline.
