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
    def test_health_and_browser_preflight(self):
        client = app.test_client()
        response = client.get('/api/health')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['status'], 'ok')
        for origin in ('http://localhost:4700', 'http://127.0.0.1:4700'):
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
