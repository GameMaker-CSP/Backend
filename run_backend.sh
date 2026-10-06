#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    "${PYTHON:-python3}" -m venv .venv
fi
if [ ! -f .venv/.gamemaker-ready ]; then
    .venv/bin/python -m pip install -r requirements.txt
    touch .venv/.gamemaker-ready
fi

# Create only a new local configuration; preserve a developer's existing one.
if [ ! -f .env ]; then
    .venv/bin/python - <<'PY'
import os
import secrets
from pathlib import Path

config = Path('.env.example').read_text().replace(
    'replace-with-a-random-local-secret', secrets.token_urlsafe(48)
)
fd = os.open('.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as output:
    output.write(config)
PY
fi

mkdir -p instance/volumes
exec .venv/bin/python main.py
