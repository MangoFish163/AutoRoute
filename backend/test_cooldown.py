import os
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

_data = tempfile.TemporaryDirectory()
os.environ['LOCAL_MODEL_ROUTER_HOME'] = _data.name
import app as router


class CooldownTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        self.paths = patch.multiple(router, DB_PATH=home / 'router.db', BOOTSTRAP_KEY_PATH=home / 'client.key')
        self.paths.start()
        router.init_db()
        self.body = dict(name='Test', alias='test', provider='openai', base_url='https://example.test/v1',
                         upstream_model='test-model', api_keys=['test-key-1', 'test-key-2'], auto_weight=0.81)
        self.dep = router.create_deployment(router.DeploymentInput(**self.body))
        self.other = router.create_deployment(router.DeploymentInput(**{**self.body, 'alias': 'other'}))
        for dep in (self.dep, self.other):
            with router.connect() as db:
                db.execute('UPDATE deployment_state SET cooldown_until=?,consecutive_failures=3,successes=9,failures=4 WHERE deployment_id=?',
                           (time.time() + 3600, dep['id']))
            for index in (0, 1):
                with router.connect() as db:
                    db.execute('INSERT INTO credential_state VALUES(?,?,?,?,?)', (dep['id'], index, time.time() + 3600, 3, router.now_iso()))

    def tearDown(self):
        self.paths.stop()
        self.tmp.cleanup()

    async def test_edit_without_replacing_keys_clears_all_cooldowns(self):
        self.assertEqual(router.candidates('test'), [])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=router.app), base_url='http://test') as client:
            response = await client.put('/api/deployments/' + self.dep['id'], json={
                **self.body, 'base_url': 'https://fixed.example.test/v1', 'api_keys': []})
            self.assertEqual(response.status_code, 200, response.text)
            updated = response.json()
            self.assertEqual(updated['health'], 'ready')
            self.assertEqual(updated['available_keys'], 2)
            self.assertEqual(updated['consecutive_failures'], 0)
            self.assertEqual(updated['cooldown_until'], 0)
            self.assertEqual((updated['successes'], updated['failures']), (9, 4))
            self.assertEqual(updated['auto_weight'], 1)
            deployments = (await client.get('/api/deployments')).json()
            self.assertEqual(next(d for d in deployments if d['id'] == self.dep['id'])['health'], 'ready')
        pool = router.candidates('test')
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0]['api_keys'], self.body['api_keys'])
        self.assertEqual(router.get_state(self.other['id'])['health'], 'cooldown')
        self.assertEqual(router.available_key_indices(self.other['id'], 2), [])

    async def test_replacing_keys_also_clears_deployment_cooldown(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=router.app), base_url='http://test') as client:
            response = await client.put('/api/deployments/' + self.dep['id'], json={**self.body, 'api_keys': ['new-test-key']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['health'], 'ready')
        self.assertEqual(router.candidates('test')[0]['api_keys'], ['new-test-key'])

    async def test_rejected_edit_leaves_cooldowns_intact(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=router.app), base_url='http://test') as client:
            response = await client.put('/api/deployments/' + self.dep['id'], json={**self.body, 'base_url': ''})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(router.get_state(self.dep['id'])['health'], 'cooldown')
        self.assertEqual(router.available_key_indices(self.dep['id'], 2), [])


class RoutingPolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        self.paths = patch.multiple(router, DB_PATH=home / 'router.db', BOOTSTRAP_KEY_PATH=home / 'client.key')
        self.paths.start()
        router.init_db()
        self.body = dict(name='Test', alias='test', base_url='https://example.test/v1', upstream_model='test',
                         api_keys=['test-key'], use_system_proxy=False)
        self.dep = router.create_deployment(router.DeploymentInput(**self.body))

    def tearDown(self):
        self.paths.stop()
        self.tmp.cleanup()

    def save(self, **changes):
        self.body.update(changes)
        self.dep = router.update_deployment(self.dep['id'], router.DeploymentInput(**self.body))
        return self.dep

    def fail_request(self, status=500):
        router.record_result(self.dep['id'], False, status, key_index=0, config_revision=self.dep['config_revision'])
        return router.list_deployments()[0]

    async def test_every_identical_save_resets_again_and_rejects_old_results(self):
        for cycle in range(3):
            old_revision = self.dep['config_revision']
            for _ in range(3):
                self.fail_request(401)
            self.assertEqual(router.get_state(self.dep['id'])['health'], 'cooldown')
            with router.connect() as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES(?, '1')", (f"key_cursor:{self.dep['id']}",))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=router.app), base_url='http://test') as client:
                response = await client.put('/api/deployments/' + self.dep['id'], json=self.body)
            self.assertEqual(response.status_code, 200, response.text)
            self.dep = response.json()
            self.assertEqual(self.dep['config_revision'], old_revision + 1)
            self.assertEqual(self.dep['health'], 'ready')
            self.assertEqual(self.dep['auto_weight'], 1)
            self.assertEqual(self.dep['available_keys'], 1)
            self.assertEqual(self.dep['consecutive_failures'], 0)
            router.record_result(self.dep['id'], False, 401, key_index=0, config_revision=old_revision)
            self.assertEqual(router.get_state(self.dep['id'])['health'], 'ready')
            with router.connect() as db:
                self.assertIsNone(db.execute('SELECT 1 FROM settings WHERE key=?', (f"key_cursor:{self.dep['id']}",)).fetchone())

    def test_first_two_failures_only_reduce_weight_for_every_error_class(self):
        for status in (400, 401, 403, 429, 500, 502, 408):
            with self.subTest(status=status):
                self.save()
                previous_weight = 1
                for _ in range(2):
                    dep = self.fail_request(status)
                    self.assertEqual(dep['health'], 'degraded')
                    self.assertEqual(dep['available_keys'], 1)
                    self.assertLess(dep['effective_weight'], previous_weight)
                    self.assertEqual(len(router.candidates('test')), 1)
                    previous_weight = dep['effective_weight']
                self.assertEqual(self.fail_request(status)['health'], 'cooldown')

    def test_effective_weight_50_and_above_never_cools_on_that_failure(self):
        for base in (50, 75, 100):
            self.save(base_weight=base)
            for _ in range(10):
                before = router.list_deployments()[0]['effective_weight']
                dep = self.fail_request(429)
                if before >= 50:
                    self.assertNotEqual(dep['health'], 'cooldown')
                    self.assertEqual(dep['available_keys'], 1)
                    self.assertEqual(len(router.candidates('test')), 1)
                    self.assertLess(dep['effective_weight'], before)
                elif dep['consecutive_failures'] >= 3:
                    self.assertEqual(dep['health'], 'cooldown')
                    break
            else:
                self.fail('did not cool after dropping below 50')

    def test_locked_high_weight_stays_available_and_locked_low_weight_can_cool(self):
        self.save(base_weight=50, weight_locked=True)
        for _ in range(6):
            dep = self.fail_request(401)
            self.assertEqual(dep['effective_weight'], 50)
            self.assertNotEqual(dep['health'], 'cooldown')
            self.assertEqual(dep['available_keys'], 1)
        self.save(base_weight=49, weight_locked=True)
        for _ in range(3):
            dep = self.fail_request(401)
        self.assertEqual(dep['health'], 'cooldown')

    def test_success_restarts_failure_streak_and_threshold_has_minimum_three(self):
        self.assertEqual(router.save_rules({'failure_threshold': 1})['failure_threshold'], 3)
        with router.connect() as db:
            db.execute("UPDATE settings SET value=? WHERE key='rule_settings'", ('{"failure_threshold":1,"enabled":true}',))
        self.fail_request(429)
        self.fail_request(429)
        router.record_result(self.dep['id'], True, 200, key_index=0)
        self.assertEqual(self.fail_request(429)['consecutive_failures'], 1)
        self.assertNotEqual(self.fail_request(429)['health'], 'cooldown')
        self.assertEqual(self.fail_request(429)['health'], 'cooldown')

    def test_rules_disabled_never_cools_keys_or_deployment(self):
        router.save_rules({'enabled': False})
        for _ in range(5):
            dep = self.fail_request(401)
            self.assertNotEqual(dep['health'], 'cooldown')
            self.assertEqual(dep['effective_weight'], 1)
            self.assertEqual(dep['available_keys'], 1)

    def test_old_schema_adds_revision_without_changing_deployment(self):
        with router.connect() as db:
            db.execute('ALTER TABLE deployments DROP COLUMN config_revision')
        router.init_db()
        dep = router.list_deployments()[0]
        self.assertEqual(dep['config_revision'], 0)
        self.assertEqual(dep['id'], self.dep['id'])
        self.assertEqual(dep['key_count'], 1)

    def test_old_high_weight_cooldowns_do_not_exclude_model_or_keys(self):
        self.save(base_weight=100, weight_locked=True)
        with router.connect() as db:
            db.execute('UPDATE deployment_state SET consecutive_failures=9,cooldown_until=? WHERE deployment_id=?', (time.time() + 3600, self.dep['id']))
            db.execute('INSERT INTO credential_state VALUES(?,?,?,?,?)', (self.dep['id'], 0, time.time() + 3600, 9, router.now_iso()))
        self.assertNotEqual(router.list_deployments()[0]['health'], 'cooldown')
        self.assertEqual(len(router.candidates('test')), 1)

    async def test_inflight_failure_cannot_reapply_state_after_save(self):
        entered, resume = asyncio.Event(), asyncio.Event()
        async def upstream(request):
            entered.set()
            await resume.wait()
            return httpx.Response(401, text='test invalid token')
        real_client = httpx.AsyncClient
        with patch.object(router.httpx, 'AsyncClient', side_effect=lambda **_: real_client(transport=httpx.MockTransport(upstream))):
            pending = asyncio.create_task(router.forward({'model': 'test', 'messages': []}, 'openai', 'request-before-save'))
            await asyncio.wait_for(entered.wait(), timeout=5)
            self.save()
            resume.set()
            with self.assertRaises(router.HTTPException):
                await pending
        state = router.get_state(self.dep['id'])
        self.assertEqual(state['consecutive_failures'], 0)
        self.assertEqual(state['health'], 'ready')
        self.assertEqual(router.list_deployments()[0]['auto_weight'], 1)

    async def test_three_failed_stream_requests_count_once_each(self):
        self.save(provider='responses')
        real_client = httpx.AsyncClient
        def upstream(request):
            return httpx.Response(200, content=b'data: {"type":"response.failed","response":{"error":{"message":"failure"}}}\n\n',
                                  headers={'content-type': 'text/event-stream'})
        with patch.object(router.httpx, 'AsyncClient', side_effect=lambda **_: real_client(transport=httpx.MockTransport(upstream))):
            for number in range(1, 4):
                response = await router.forward_stream({'model': 'test', 'messages': [], 'stream': True}, 'openai', f'stream-{number}')
                with self.assertRaises(ValueError):
                    async for _ in response.body_iterator:
                        pass
                self.assertEqual(router.get_state(self.dep['id'])['consecutive_failures'], number)
        self.assertEqual(router.get_state(self.dep['id'])['health'], 'cooldown')


if __name__ == '__main__':
    unittest.main()
