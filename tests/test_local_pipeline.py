"""Real Flask/SQLite contract tests, isolated from developer data."""
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='gamemaker-tests-')
os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(TEST_DIRECTORY.name) / 'test.db')
os.environ['SECRET_KEY'] = 'isolated-local-pipeline-test-secret-2026'
os.environ['IS_PRODUCTION'] = 'false'

from main import app


class LocalPipelineTests(unittest.TestCase):
    def test_display_preferences_follow_the_account(self):
        path = '/api/user/preferences'
        settings = {'theme': 'Ocean', 'language': 'es', 'nod': True}
        guest = app.test_client()
        self.assertEqual(guest.put(path, json=settings).status_code, 401)
        for uid in ('theme-owner', 'theme-other'):
            self.assertIn(guest.post('/api/user', json={'uid': uid, 'name': uid,
                          'password': 'Test-Preferences-2026'}).status_code, (200, 201))

        def sign_in(uid):
            device = app.test_client()
            self.assertEqual(device.post('/api/otp/send', json={'uid': uid,
                             'password': 'Test-Preferences-2026'}).status_code, 200)
            return device

        desktop = sign_in('theme-owner')
        self.assertEqual(desktop.put(path, json=settings).json, settings)
        self.assertEqual(desktop.put(path, json={**settings, 'nod': 'false'}).status_code, 400)
        self.assertEqual(sign_in('theme-owner').get(path).json, settings)
        self.assertFalse(sign_in('theme-other').get(path).json['nod'])

        extended = path + '?extended=1'
        options = {'size': 19, 'bg': '#123456', 'fontName': 'Georgia', 'ruler': True,
                   'mode': 'cursor', 'ttsVolume': 0, 'buttonStyle': 'pill'}
        self.assertEqual(desktop.put(path, json={**settings, 'options':options}).json['options'], options)
        self.assertEqual(desktop.put(path, json=settings).status_code, 200)
        self.assertEqual(desktop.put(path, json={**settings, 'options':{'size':200}}).status_code, 400)
        self.assertEqual(sign_in('theme-owner').get(extended).json['options'], options)
        self.assertEqual(sign_in('theme-other').get(extended).json['options'], {})

    def test_manager_roles_accounts_and_guest_window(self):
        from unittest.mock import patch
        from model.user import User

        def sign_in(prefix):
            client = app.test_client()
            result = client.post('/login', data={'username': app.config[prefix + '_UID'],
                                                 'password': app.config[prefix + '_PASSWORD']})
            self.assertEqual(result.status_code, 302)
            return client

        admin, teacher, member = (sign_in(prefix) for prefix in ('ADMIN', 'TEACHER', 'USER'))
        mentor = {'accountType':'mentor', 'uid':'event-mentor', 'name':'Taylor Ellis', 'password':'Initial-test-password'}
        self.assertEqual(member.post('/api/user/admin-create', json=mentor).status_code, 403)
        self.assertFalse(teacher.post('/api/user/admin-create', json=mentor).json['active'])
        self.assertTrue(admin.post('/api/user/event-mentor/verify').json['active'])
        uid = 'manager-test-member'
        public = app.test_client()
        credentials = {'uid': uid, 'password': 'Isolated-test-password'}
        created = public.post('/api/user', json=dict(credentials, name='Taylor Ellis'))
        self.assertEqual(created.status_code, 200)
        self.assertNotIn('password', created.json)
        member_id = created.json['id']
        for route in ('/users/table2', '/persona/', '/sections/'):
            self.assertEqual(admin.get(route).status_code, 200)
        self.assertNotIn(b'Taylor Ellis', member.get('/users/table2').data)
        self.assertEqual(member.delete(f'/users/delete/{member_id}').status_code, 403)
        role_url = f'/api/user/{uid}/role'
        self.assertEqual(teacher.post(role_url, json={'role':'Admin'}).status_code, 403)
        self.assertEqual(teacher.post(role_url, json={'role':'Mentor'}).json['role'], 'Mentor')
        self.assertEqual(admin.put(f'/update_user/{uid}', json={'name':'Taylor Rivers'}).status_code, 200)
        self.assertEqual(public.post('/api/authenticate', json=credentials).status_code, 200)
        self.assertEqual(admin.post(f'/api/user/{uid}/deactivate').status_code, 200)
        self.assertEqual(public.get('/api/id').status_code, 401)
        self.assertEqual(public.post('/api/authenticate', json=credentials).status_code, 401)
        self.assertEqual(teacher.post(f'/api/user/{uid}/verify', json={'days':30}).status_code, 200)
        self.assertEqual(public.post('/api/authenticate', json=credentials).status_code, 200)
        self.assertEqual(admin.post(f'/users/reset_password/{member_id}').status_code, 200)
        self.assertEqual(public.post('/api/authenticate', json=credentials).status_code, 401)
        self.assertEqual(admin.delete(f'/users/delete/{member_id}').status_code, 200)
        with app.app_context():
            self.assertIsNone(User.query.filter_by(_uid=uid).first())

        section = {'name':'Event volunteers', 'abbreviation':'EV'}
        self.assertEqual(member.post('/api/section', json=section).status_code, 403)
        created = admin.post('/api/section', json=section)
        self.assertEqual(created.status_code, 200)
        route = '/api/section/' + str(created.json['id'])
        self.assertEqual(admin.put(route, json=dict(section, name='Event team')).status_code, 200)
        self.assertEqual(admin.delete(route).status_code, 200)

        with patch.dict(app.config, GUEST_WINDOW_FILE=str(Path(TEST_DIRECTORY.name) / 'guest-test.json')):
            guest = app.test_client()
            guest_credentials = {'uid':'weekend-guest','password':'Guest-test-password'}
            self.assertEqual(guest.post('/api/user/guest', json=guest_credentials).status_code, 403)
            self.assertEqual(teacher.post('/api/guest/window', json={'minutes':10}).status_code, 200)
            self.assertEqual(guest.post('/api/user/guest', json=guest_credentials).json['role'], 'Guest')
            self.assertEqual(guest.post('/api/authenticate', json=guest_credentials).status_code, 200)
            self.assertEqual(guest.get('/api/id').status_code, 200)
            self.assertEqual(admin.post('/api/guest/disable').status_code, 200)
            self.assertEqual(guest.get('/api/id').status_code, 401)

    def test_health_and_browser_preflight(self):
        client = app.test_client()
        response = client.get('/api/health')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['status'], 'ok')
        for origin in ('http://localhost:4700', 'http://127.0.0.1:4700', 'https://ueslgame.opencodingsociety.com'):
            with self.subTest(origin=origin):
                response = client.options('/api/game/save', headers={
                    'Origin': origin,
                    'Access-Control-Request-Method': 'POST',
                    'Access-Control-Request-Headers': 'content-type',
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['Access-Control-Allow-Origin'], origin)
                self.assertEqual(response.headers['Access-Control-Allow-Credentials'], 'true')
        response = client.options('/api/game/save', headers={
            'Origin': 'https://untrusted.example',
            'Access-Control-Request-Method': 'POST',
        })
        self.assertNotIn('Access-Control-Allow-Origin', response.headers)

    def test_game_round_trip_and_account_isolation(self):
        owner, other = app.test_client(), app.test_client()
        self.assertEqual(owner.get('/api/game/list').status_code, 401)
        for client, uid in ((owner, 'pipeline-owner'), (other, 'pipeline-other')):
            response = client.post('/api/user', json={
                'name': uid, 'uid': uid, 'password': 'Pipeline-Test-2026',
            })
            self.assertIn(response.status_code, (200, 201))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                response = client.post('/api/authenticate', json={
                    'uid': uid, 'password': 'Pipeline-Test-2026',
                })
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('Token set:', output.getvalue())
            self.assertEqual(client.get('/api/id').status_code, 200)

        payload = {'game': {'title': 'Teacher Demo', 'levels': []}, 'nextId': 1}
        response = owner.post('/api/game/save', json={
            'name': 'Teacher Demo', 'game_data': json.dumps(payload),
        })
        self.assertEqual(response.status_code, 200)
        game_id = response.json['id']
        self.assertTrue(any(g['id'] == game_id for g in owner.get('/api/game/list').json['games']))
        loaded = owner.get(f'/api/game/load/{game_id}')
        self.assertEqual(json.loads(loaded.json['game_data']), payload)
        self.assertEqual(other.get(f'/api/game/load/{game_id}').status_code, 404)
        self.assertEqual(other.delete(f'/api/game/delete/{game_id}').status_code, 404)

        payload['game']['title'] = 'Updated Teacher Demo'
        response = owner.post('/api/game/save', json={
            'name': 'Teacher Demo', 'game_data': payload,
        })
        self.assertEqual(response.json['id'], game_id)
        loaded = owner.get(f'/api/game/load/{game_id}')
        self.assertEqual(json.loads(loaded.json['game_data']), payload)
        self.assertEqual(owner.post('/api/game/save', json={'name': ''}).status_code, 400)
        self.assertEqual(owner.delete(f'/api/game/delete/{game_id}').status_code, 200)
        self.assertEqual(owner.get(f'/api/game/load/{game_id}').status_code, 404)
        self.assertEqual(owner.delete('/api/authenticate').status_code, 200)
        self.assertEqual(owner.get('/api/game/list').status_code, 401)

    def test_login_page_session_and_profile_persistence(self):
        client = app.test_client()
        uid = 'profile-pipeline'
        created = client.post('/api/user', json={
            'name': 'Profile Before', 'uid': uid, 'password': 'Profile-Test-2026',
        })
        self.assertIn(created.status_code, (200, 201))
        self.assertEqual(client.put('/api/user', json={'name': 'Blocked'}).status_code, 401)
        invalid = client.post('/api/otp/send', json={'uid': uid, 'password': 'wrong'})
        self.assertEqual(invalid.status_code, 401)
        login = client.post('/api/otp/send', json={'uid': uid, 'password': 'Profile-Test-2026'})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json['user']['uid'], uid)
        saved = client.put('/api/user', json={'name': 'Profile After'})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json['name'], 'Profile After')
        self.assertEqual(client.get('/api/id').json['name'], 'Profile After')
        self.assertEqual(client.delete('/api/authenticate').status_code, 200)
        self.assertEqual(client.get('/api/id').status_code, 401)

    def test_ocs_survey_persists_and_is_private_to_the_account(self):
        owner, other = app.test_client(), app.test_client()
        self.assertEqual(owner.get('/api/user/personas').status_code, 401)
        for client, uid in ((owner, 'survey-owner'), (other, 'survey-other')):
            self.assertIn(client.post('/api/user', json={
                'name': uid, 'uid': uid, 'password': 'Survey-Test-2026',
            }).status_code, (200, 201))
            self.assertEqual(client.post('/api/otp/send', json={
                'uid': uid, 'password': 'Survey-Test-2026',
            }).status_code, 200)
        catalogue = owner.get('/api/persona?collection=gamemaker').json
        self.assertEqual(len(catalogue), 16)
        self.assertTrue(all(item['alias'].startswith('gm-') for item in catalogue))
        categories = {item['category'] for item in catalogue}
        self.assertEqual(categories, {'student', 'social', 'achievement', 'fantasy'})
        picks = {}
        for category in categories:
            options = [item for item in catalogue if item['category'] == category]
            for option in options[:2]:
                saved = owner.post('/api/user/persona', json={'persona_id': option['id']})
                self.assertIn(saved.status_code, (200, 201))
                picks[category] = option['id']
        owner.delete('/api/authenticate')
        self.assertEqual(owner.post('/api/otp/send', json={
            'uid': 'survey-owner', 'password': 'Survey-Test-2026',
        }).status_code, 200)
        saved = owner.get('/api/user/personas').json
        self.assertEqual(saved['total_selected'], 4)
        self.assertEqual({k: v['persona_id'] for k, v in saved['personas'].items()}, picks)
        self.assertEqual(other.get('/api/user/personas').json['total_selected'], 0)

    def test_legacy_database_is_never_deleted_on_startup(self):
        database = Path(TEST_DIRECTORY.name) / 'legacy.db'
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE games (id INTEGER PRIMARY KEY, game_data TEXT)')
            connection.execute("INSERT INTO games VALUES (1, 'keep this game')")
        environment = dict(os.environ, DATABASE_URL='sqlite:///' + str(database))
        result = subprocess.run(
            [sys.executable, '-c', 'import main'], env=environment,
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Legacy games schema detected', result.stderr)
        with sqlite3.connect(database) as connection:
            self.assertEqual(connection.execute('SELECT game_data FROM games').fetchone()[0], 'keep this game')


if __name__ == '__main__':
    unittest.main()
