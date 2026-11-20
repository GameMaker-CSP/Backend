#!/usr/bin/env python3
"""Generate deployment-only secrets without printing or replacing existing ones."""
import os
from pathlib import Path
import secrets
import sys

mode = sys.argv[1] if len(sys.argv) == 2 else ""
if mode not in ("local", "production"):
    raise SystemExit("Usage: python3 deploy/init_env.py local|production")
target = Path(__file__).with_name('.env')
values = {key: secrets.token_urlsafe(32) for key in (
    'SECRET_KEY', 'DEFAULT_PASSWORD', 'ADMIN_PASSWORD', 'TEACHER_PASSWORD',
    'USER_PASSWORD', 'MY_PASSWORD')}
values.update(IS_PRODUCTION=str(mode == 'production').lower(), USER_UID='toby', ADMIN_UID='hop',
              USER_NAME='Thomas Edison', ADMIN_USER='Grace Hopper')
try:
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    raise SystemExit('Existing deploy/.env preserved; edit IS_PRODUCTION manually if changing mode.')
with os.fdopen(descriptor, 'w') as output:
    output.write(''.join(f'{key}={value}\n' for key, value in values.items()))
print('Created private deploy/.env. Demo credentials are stored there, not in Git.')
