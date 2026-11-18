import sqlite3
import unittest
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / 'migrations/community'


class EventSchema(unittest.TestCase):
    def test_fixture_reseed_and_rollback(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.executescript((MIGRATIONS / '001_events.up.sql').read_text())
            fixture = (1, 'test', 'Fixture only', 'Test venue', 'America/Los_Angeles',
                       '2030-01-01T18:00:00Z', '2030-01-01T19:00:00Z')
            for _ in range(2):
                db.execute('INSERT INTO uesl_event (id,event_type,title,location,timezone,'
                           'starts_at_utc,ends_at_utc) VALUES (?,?,?,?,?,?,?) '
                           'ON CONFLICT(id) DO NOTHING', fixture)
            self.assertEqual(db.execute('SELECT state FROM uesl_event').fetchall(), [('draft',)])
            db.executescript((MIGRATIONS / '001_events.down.sql').read_text())
            self.assertFalse(db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'uesl_%'").fetchall())
