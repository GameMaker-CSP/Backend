"""Run from the confirmed UESL Compose checkout in Cockpit."""
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

DOMAIN = 'ueslgame-api.opencodingsociety.com'
ORIGIN = 'https://ueslgame.opencodingsociety.com'
LOCAL = 'http://127.0.0.1:8540'


def run(*args):
    subprocess.run(args, check=True)


def check(base):
    for path, method, expected in [('/api/otp/send', 'OPTIONS', (200, 204)), ('/api/id', 'GET', (401,))]:
        headers = {'Origin': ORIGIN}
        if method == 'OPTIONS':
            headers.update({'Access-Control-Request-Method': 'POST',
                            'Access-Control-Request-Headers': 'content-type,x-origin'})
        req = urllib.request.Request(base + path, method=method, headers=headers)
        try:
            response = urllib.request.urlopen(req, timeout=6)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            h = response.headers
            if (response.status not in expected or h.get_all('Access-Control-Allow-Origin') != [ORIGIN]
                    or h.get_all('Access-Control-Allow-Credentials') != ['true']):
                raise ValueError(f'{base}{path} returned unexpected status or login headers')
            if method == 'OPTIONS':
                for key, needed in [('Methods', {'post'}), ('Headers', {'content-type', 'x-origin'})]:
                    allowed = h.get('Access-Control-Allow-' + key, '').lower().split(',')
                    if not needed.issubset(v.strip() for v in allowed):
                        raise ValueError(f'{base} did not allow the requested {key}')


def retry(base):
    for attempt in range(15):
        try:
            check(base)
            return
        except (OSError, ValueError):
            if attempt == 14:
                raise
            time.sleep(2)


def patch_site(source):
    # Edit only standalone CORS directives in a file dedicated to this hostname.
    names = re.findall(r'^\s*server_name\s+([^;]+);', source, re.M)
    if not names or any(n.strip() != DOMAIN for n in names):
        raise ValueError('Expected a dedicated UESL Nginx file')
    if not re.search(r'(?m)^\s*listen\s+(?:[^;\s]+:)?443\b', source):
        raise ValueError('Could not identify the HTTPS listener')
    cors = r'''(?im)^[ \t]*add_header\s+["']?Access-Control-[\w-]+["']?\s+[^;\n]+;[ \t]*(?:#[^\n]*)?$'''
    updated = re.sub(cors, '', source)
    # Remove an OPTIONS shortcut only when its entire remaining body is a 2xx return.
    return re.sub(r'''(?im)^[ \t]*if\s*\(\s*\$request_method\s*=\s*["']?OPTIONS["']?\s*\)\s*\{\s*return\s+20[04];\s*\}''', '', updated)


def main():
    root = Path('/home/ubuntu/newuesl_backend')
    os.chdir(root)
    labels = json.loads(subprocess.check_output(['docker', 'inspect', '-f',
                        '{{json .Config.Labels}}', 'newuesl_backend-web-1']))
    if (labels.get('com.docker.compose.project.working_dir') != str(root)
            or labels.get('com.docker.compose.service') != 'web'):
        raise ValueError('Unexpected Compose project')
    run('sudo', 'nginx', '-t')
    files = subprocess.check_output(['sudo', 'grep', '-lF', DOMAIN, *Path('/etc/nginx/sites-enabled').iterdir()], text=True).splitlines()
    if len(files) != 1:
        raise ValueError('Could not uniquely identify the enabled UESL site')
    site = Path(files[0]).resolve()
    source = subprocess.check_output(['sudo', 'cat', site], text=True)
    updated = patch_site(source)
    env = root / '.env'
    original = env.read_text()
    configured = original
    # Preserve other settings. Refuse complex expressions in the two edited keys.
    for key in ('IS_PRODUCTION', 'CORS_ORIGINS'):
        pattern = rf'(?m)^[ \t]*(?:export\s+)?{key}\s*=([^\n]*)$'
        matches = re.findall(pattern, configured)
        if len(matches) > 1:
            raise ValueError(f'Duplicate {key} settings')
        value = matches[0].split('#')[0].strip().strip("\042\047") if matches else ''
        if key == 'CORS_ORIGINS':
            if not value or ORIGIN in [v.strip() for v in value.split(',')]:
                continue
            if not re.fullmatch(r'[\w:/., -]+', value):
                raise ValueError('Custom CORS_ORIGINS needs manual review')
            value += ',' + ORIGIN
        else:
            value = 'true'
        line = f'{key}={value}'
        configured = re.sub(pattern, lambda _: line, configured) if matches else configured.rstrip('\n') + '\n' + line + '\n'
    backup = subprocess.check_output(['sudo', 'mktemp', '-d', '/var/backups/uesl-login.XXXXXXXX'], text=True).strip()
    print('Backups:', backup, flush=True)
    run('sudo', 'cp', '-p', env, backup + '/env')
    run('sudo', 'cp', '-p', site, backup + '/nginx.conf')
    if configured != original:
        env.write_text(configured)
    run('docker', 'compose', 'up', '-d', '--build', '--no-deps', 'web')
    retry(LOCAL)
    if updated != source:
        with tempfile.NamedTemporaryFile(mode='w') as candidate:
            candidate.write(updated)
            candidate.flush()
            if subprocess.check_output(['sudo', 'cat', site], text=True) != source:
                raise ValueError('Nginx file changed during the build')
            try:
                run('sudo', 'cp', candidate.name, site)
                run('sudo', 'nginx', '-t')
                run('sudo', 'systemctl', 'reload', 'nginx')
                retry('https://' + DOMAIN)
            except BaseException:
                run('sudo', 'cp', '-p', backup + '/nginx.conf', site)
                run('sudo', 'nginx', '-t')
                run('sudo', 'systemctl', 'reload', 'nginx')
                raise
    else:
        check('https://' + DOMAIN)
    print('Public login checks passed. Sign in at ' + ORIGIN + '/login')


if __name__ == '__main__':
    main()
