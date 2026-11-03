import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import httpx
import app as router


class ManagementAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        self.paths = patch.multiple(router, DB_PATH=home / 'router.db', BOOTSTRAP_KEY_PATH=home / 'client.key')
        self.paths.start()
        router.init_db()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=router.app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        self.paths.stop()
        self.tmp.cleanup()

    async def test_every_management_route_requires_token_before_validation(self):
        for route in router.app.routes:
            if route.path.startswith('/api/'):
                for method in route.methods:
                    with self.subTest(path=route.path, method=method):
                        response = await self.client.request(method, route.path)
                        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual((await self.client.get('/api/future-endpoint')).status_code, 401)

    async def test_client_key_bearer_query_and_duplicate_headers_do_not_grant_management_access(self):
        token = router.app.state.management_token
        for headers, query in [
            ({'X-Management-Token': 'wrong'}, ''),
            ({'X-Management-Token': router.client_key()}, ''),
            ({'Authorization': 'Bearer ' + token}, ''),
            ({}, '?management_token=' + token),
            ([('X-Management-Token', token), ('X-Management-Token', token)], ''),
        ]:
            response = await self.client.get('/api/bootstrap' + query, headers=headers)
            self.assertEqual(response.status_code, 401)
            self.assertNotIn(token, response.text)

    async def test_management_token_is_separate_from_model_credentials(self):
        token = router.app.state.management_token
        response = await self.client.get('/api/bootstrap', headers={'X-Management-Token': token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.json()['client_key'], router.client_key())
        self.assertNotIn(token, response.text)
        self.assertNotIn(token, (await self.client.get('/health')).text)
        for path in ['/v1/models', '/v1/chat/completions', '/v1/messages']:
            response = await self.client.request('GET' if path.endswith('models') else 'POST', path,
                                                 headers={'Authorization': 'Bearer ' + token}, json={})
            self.assertEqual(response.status_code, 401)
        response = await self.client.get('/v1/models', headers={'Authorization': 'Bearer ' + router.client_key()})
        self.assertEqual(response.status_code, 200)

    async def test_cors_preflight_allows_management_header_but_does_not_authenticate(self):
        headers = {'Origin': 'http://tauri.localhost', 'Access-Control-Request-Method': 'GET',
                   'Access-Control-Request-Headers': 'x-management-token,content-type'}
        self.assertEqual((await self.client.options('/api/bootstrap', headers=headers)).status_code, 200)
        self.assertEqual((await self.client.get('/api/bootstrap', headers={'Origin': headers['Origin']})).status_code, 401)
        headers['Origin'] = 'https://untrusted.example'
        self.assertEqual((await self.client.options('/api/bootstrap', headers=headers)).status_code, 400)


class DesktopTokenLifecycleTests(unittest.TestCase):
    def test_missing_private_pipe_token_fails_closed(self):
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, LOCAL_MODEL_ROUTER_HOME=home, LOCAL_MODEL_ROUTER_DESKTOP_INSTANCE='auth-test')
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('desktop_entry.py'))],
                                    input=b'', capture_output=True, env=env, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'Missing or invalid desktop management token', result.stderr)
            self.assertFalse((Path(home) / 'client.key').exists())

    def test_private_pipe_restart_rotation_and_authenticated_log_stream(self):
        with tempfile.TemporaryDirectory() as home:
            previous_token = None
            client_key = None
            for _ in range(2):
                with socket.socket() as probe:
                    probe.bind(('127.0.0.1', 0))
                    port = probe.getsockname()[1]
                token = secrets.token_hex(32)
                env = dict(os.environ, LOCAL_MODEL_ROUTER_HOME=home, LOCAL_MODEL_ROUTER_DESKTOP_INSTANCE='auth-test')
                process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('desktop_entry.py')), '--port', str(port)],
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
                try:
                    process.stdin.write((token + '\n').encode())
                    process.stdin.flush()
                    base = f'http://127.0.0.1:{port}'
                    for attempt in range(100):
                        if process.poll() is not None:
                            self.fail('Desktop backend exited during startup')
                        try:
                            with urllib.request.urlopen(base + '/health', timeout=.5) as response:
                                self.assertNotIn(token, response.read().decode())
                            break
                        except OSError:
                            time.sleep(.1)
                    else:
                        self.fail('Desktop backend startup timed out')
                    for rejected in [None, previous_token or 'wrong']:
                        request = urllib.request.Request(base + '/api/bootstrap', headers={'X-Management-Token': rejected} if rejected else {})
                        with self.assertRaises(urllib.error.HTTPError) as caught:
                            urllib.request.urlopen(request, timeout=2)
                        self.assertEqual(caught.exception.code, 401)
                        caught.exception.close()
                    request = urllib.request.Request(base + '/api/bootstrap', headers={'X-Management-Token': token})
                    with urllib.request.urlopen(request, timeout=2) as response:
                        body = json.load(response)
                    if client_key:
                        self.assertEqual(body['client_key'], client_key)
                    client_key = body['client_key']
                    request = urllib.request.Request(base + '/api/events/stream?start=0&end=1', headers={'X-Management-Token': token})
                    with urllib.request.urlopen(request, timeout=2) as response:
                        self.assertEqual(response.readline(), b'event: ready\n')
                    process.communicate(b'stop\n', timeout=15)
                    self.assertEqual(process.returncode, 0)
                    self.assertEqual(sorted(p.name for p in Path(home).iterdir()), ['client.key', 'router.db'])
                    previous_token = token
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate()


if __name__ == '__main__':
    unittest.main()
