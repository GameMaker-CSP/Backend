"""Deployment checks use a temporary database and never touch developer accounts."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class DeploymentTests(unittest.TestCase):
    def test_rejects_missing_production_secrets(self):
        env = dict(os.environ, DEPLOYMENT_MODE='true', SECRET_KEY='short')
        result = subprocess.run([sys.executable, '-c', 'import __init__'], cwd=ROOT,
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Deployment requires generated secrets', result.stderr)

    def test_deployment_login_cookie_and_runner_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, DEPLOYMENT_MODE='true', IS_PRODUCTION='true',
                       DATABASE_URL='sqlite:///' + str(Path(directory)/'deployment.db'))
            for key in ('SECRET_KEY', 'DEFAULT_PASSWORD', 'ADMIN_PASSWORD',
                        'TEACHER_PASSWORD', 'USER_PASSWORD', 'MY_PASSWORD'):
                env[key] = 'Deployment-isolated-test-2026'
            code = '''
from main import app
client = app.test_client()
assert client.get('/api/health').status_code == 200
for route in ('/run/python', '/run/javascript'):
    assert client.post(route, json={'code':'print(1)'}).status_code == 404
for route in ('/api/authenticate', '/api/otp/send'):
    response = client.post(route, json={'uid':app.config['USER_UID'], 'password':app.config['USER_PASSWORD']})
    assert response.status_code == 200, response.json
    cookie = response.headers['Set-Cookie']
    assert 'Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=Lax' in cookie
    assert 'Domain=' not in cookie
'''
            result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_add_missing_environment_accounts_to_existing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, DATABASE_URL='sqlite:///' + str(Path(directory)/'accounts.db'),
                       USER_UID='original-member', ADMIN_UID='original-admin',
                       USER_PASSWORD='test-member-password', ADMIN_PASSWORD='test-admin-password')
            def run(code):
                result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env,
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
            run('from main import app')
            env.update(USER_UID='hop', USER_NAME='Grace Hopper', ADMIN_UID='toby', ADMIN_USER='Thomas Edison')
            run("""
from main import app, db, User
with app.app_context():
    assert User.query.filter_by(_uid='original-member').one()
    for key, uid, role in [('USER', 'hop', 'User'), ('ADMIN', 'toby', 'Admin')]:
        account = User.query.filter_by(_uid=uid).one()
        assert account.role == role and account.is_password(app.config[key + '_PASSWORD'])
    hop = User.query.filter_by(_uid='hop').one()
    hop.name = 'New display name'
    hop.set_password('changed-in-manager')
    db.session.commit()
""")
            run("""
from main import app, User
with app.app_context():
    hop = User.query.filter_by(_uid='hop').one()
    assert hop.name == 'New display name' and hop.is_password('changed-in-manager')
""")
