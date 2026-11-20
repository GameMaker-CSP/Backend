"""Configure the root Compose deployment and check both demo sign-ins."""
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
ROLES = (("USER", "USER_NAME", "toby", "Thomas Edison", "User"),
         ("ADMIN", "ADMIN_USER", "hop", "Grace Hopper", "Admin"))


def configure(path, current):
    contents = path.read_text()
    updated = {}
    credentials = {}
    for prefix, name_key, uid, name, _ in ROLES:
        uid_key, pw_key = prefix + "_UID", prefix + "_PASSWORD"
        previous = current.get(pw_key) or ""
        updated.update({uid_key: uid, name_key: name})
        if current.get(uid_key) == uid and len(previous) >= 20:
            credentials[pw_key] = previous
        else:
            credentials[pw_key] = updated[pw_key] = secrets.token_urlsafe(22)
    keys = "|".join(map(re.escape, updated))
    contents = re.sub(r"(?m)^[ \t]*(?:export[ \t]+)?(?:" + keys + r")[ \t]*=[^\n]*(?:\n|$)", "", contents)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as tmp:
        tmp.write(contents.rstrip("\n") + "\n")
        for key, value in updated.items():
            tmp.write(f"{key}='{value}'\n")
    os.replace(tmp.name, path)
    return {**updated, **credentials}


def main():
    os.chdir(ROOT)
    compose = ["docker", "compose"]
    config = json.loads(subprocess.check_output(compose + ["config", "--format", "json"]))
    settings = configure(ROOT / ".env", config["services"]["web"]["environment"])
    subprocess.run(compose + ["up", "-d", "--build", "--force-recreate", "--no-deps", "web"], check=True)
    for _ in range(60):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8540/api/health", timeout=2) as response:
                if json.load(response).get("status") == "ok":
                    break
        except (OSError, ValueError):
            pass
        time.sleep(1)
    else:
        raise SystemExit("Backend health check failed. Run docker compose logs --tail=50 web.")
    # Verify against the container database before displaying credentials.
    check = """
import os
from main import app
for prefix, role in [('USER', 'User'), ('ADMIN', 'Admin')]:
    uid, pw = os.environ[prefix+'_UID'], os.environ[prefix+'_PASSWORD']
    with app.test_client() as client:
        client.post('/api/otp/send', json={'uid': uid, 'password': pw})
        identity = client.get('/api/id').get_json() or {}
        if identity.get('uid') != uid or identity.get('role') != role:
            raise SystemExit(f'{uid}: frontend sign-in or role check failed. Check Accounts.')
    with app.test_client() as client:
        client.post('/login', data={'username': uid, 'password': pw})
        if client.get('/users/table2').status_code != 200:
            raise SystemExit(f'{uid}: manager sign-in failed.')
"""
    subprocess.run(compose + ["exec", "-T", "web", "python", "-c", check], check=True)
    for prefix, _, uid, _, role in ROLES:
        print(f"{uid} ({role}) password: {settings[prefix + '_PASSWORD']}")


if __name__ == "__main__":
    main()
