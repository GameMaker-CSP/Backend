#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

printf 'Preparing the GameMaker backend...\n'
if [ ! -x .venv/bin/python ]; then
    printf 'Creating the local Python environment...\n'
    "${PYTHON:-python3}" -m venv .venv
fi
if [ ! -f .venv/.gamemaker-ready ]; then
    printf 'Installing backend dependencies (the first run can take a few minutes)...\n'
    .venv/bin/python -m pip install -r requirements.txt
    touch .venv/.gamemaker-ready
fi

# Create only a new local configuration; preserve a developer's existing one.
if [ ! -f .env ]; then
    printf 'Creating the local backend configuration...\n'
    .venv/bin/python - <<'PY'
import os
import secrets
from pathlib import Path

config = Path('.env.example').read_text().replace(
    'replace-with-a-random-local-secret', secrets.token_urlsafe(48)
).replace('replace-with-a-random-local-admin-password', secrets.token_urlsafe(32))
fd = os.open('.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as output:
    output.write(config)
PY
fi

mkdir -p instance/volumes
printf 'Loading Flask and starting the server...\n'
printf 'Keep this terminal open. Wait for "Running on" before opening the website.\n'
printf 'Press Ctrl+C when you want to stop the backend.\n'
exec .venv/bin/python -u main.py
