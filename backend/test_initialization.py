import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contextlib import asynccontextmanager

import httpx

_data = tempfile.TemporaryDirectory()
os.environ['LOCAL_MODEL_ROUTER_HOME'] = _data.name
import app as router


@asynccontextmanager
async def running_client():
    async with router.lifespan(router.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=router.app), base_url="http://test") as client:
            yield client


class InitializationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        self.paths = patch.multiple(router, DB_PATH=home / 'router.db',
                                    BOOTSTRAP_KEY_PATH=home / 'client.key')
        self.paths.start()

    def tearDown(self):
        self.paths.stop()
        self.tmp.cleanup()

    async def test_first_start_creates_schema_key_and_persisted_presets(self):
        async with running_client() as client:
            response = await client.get('/api/settings')
            self.assertEqual(response.status_code, 200)
            presets = response.json()['provider_presets']
            self.assertEqual(set(presets), {'openai', 'anthropic', 'deepseek', 'agnes',
                                           'mimo', 'openrouter', 'bigmodel'})
            self.assertEqual(presets['anthropic']['provider'], 'anthropic')
            self.assertEqual((await client.get('/api/deployments')).json(), [])
            with router.connect() as db:
                stored = db.execute("SELECT value FROM settings WHERE key='provider_presets'").fetchone()
                self.assertEqual(json.loads(stored[0]), presets)
        key = router.BOOTSTRAP_KEY_PATH.read_text()
        self.assertTrue(key.startswith('sk-local-'))
        async with running_client() as client:
            self.assertEqual((await client.get('/api/settings')).json()['provider_presets'], presets)
        self.assertEqual(router.BOOTSTRAP_KEY_PATH.read_text(), key)

    async def test_existing_database_missing_presets_is_backfilled(self):
        router.init_db()
        with router.connect() as db:
            db.execute("DELETE FROM settings WHERE key='provider_presets'")
            db.execute("INSERT INTO settings VALUES('unrelated', 'keep')")
        async with running_client() as client:
            self.assertEqual(len((await client.get('/api/settings')).json()['provider_presets']), 7)
            with router.connect() as db:
                self.assertEqual(db.execute("SELECT value FROM settings WHERE key='unrelated'").fetchone()[0], 'keep')

    async def test_restart_preserves_edits_deletions_and_explicit_empty_presets(self):
        async with running_client() as client:
            presets = (await client.get('/api/settings')).json()['provider_presets']
            presets['openai']['name'] = 'My provider'
            presets['openai']['timeout_seconds'] = 120
            del presets['deepseek']
            presets['custom'] = dict(presets['openai'], name='Custom')
            saved = await client.put('/api/settings/provider-presets', json={'presets': presets})
            self.assertEqual(saved.status_code, 200)
            expected = saved.json()['presets']
        async with running_client() as client:
            self.assertEqual((await client.get('/api/settings')).json()['provider_presets'], expected)
            self.assertEqual((await client.put('/api/settings/provider-presets', json={'presets': {}})).status_code, 200)
        async with running_client() as client:
            self.assertEqual((await client.get('/api/settings')).json()['provider_presets'], {})


if __name__ == '__main__':
    unittest.main()
