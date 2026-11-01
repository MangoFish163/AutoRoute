import os
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import httpx

_data = tempfile.TemporaryDirectory()
os.environ['LOCAL_MODEL_ROUTER_HOME'] = _data.name
import app as router

Client = httpx.AsyncClient
CUSTOM = 'http://custom-proxy.test:8080'
SYSTEM = 'http://system-proxy.test:7890'


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def check_requests(self, mode):
        cases = [
            # Enabled, saved URL, system default, expected proxy, environment fallback.
            (False, CUSTOM, SYSTEM, None, False),
            (False, '', SYSTEM, None, False),
            (False, CUSTOM, None, None, False),
            (True, CUSTOM, SYSTEM, CUSTOM, False),
            (True, f'  {CUSTOM}  ', SYSTEM, CUSTOM, False),
            (True, '', SYSTEM, SYSTEM, False),
            (True, '   ', SYSTEM, SYSTEM, False),
            (True, '', None, None, True),
        ]
        for enabled, custom, system, expected_proxy, expected_env in cases:
            with self.subTest(mode=mode, enabled=enabled, custom=custom, system=system):
                dep = dict(id='test', name='test', alias='test', supplier_name='Test provider',
                           provider='openai', upstream_model='test-model', base_url='https://upstream.test/v1',
                           api_keys=['test-key'], timeout_seconds=5, use_system_proxy=enabled, proxy_url=custom)
                received_options = []

                def upstream(request):
                    if mode == 'models':
                        self.assertEqual(request.url.path, '/v1/models')
                        return httpx.Response(200, json={'data': [{'id': 'test-model'}]})
                    self.assertEqual(request.url.path, '/v1/chat/completions')
                    if mode == 'stream':
                        return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n',
                                              headers={'content-type': 'text/event-stream'})
                    return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})

                def make_client(**options):
                    received_options.append(options)
                    return Client(transport=httpx.MockTransport(upstream), trust_env=False)

                async with Client(transport=httpx.ASGITransport(app=router.app), base_url='http://test', trust_env=False) as client:
                    with ExitStack() as stack:
                        stack.enter_context(patch.dict(os.environ, {'HTTP_PROXY': 'http://env-proxy.test:9999',
                                                                   'HTTPS_PROXY': 'http://env-proxy.test:9999',
                                                                   'ALL_PROXY': 'http://env-proxy.test:9999'}))
                        system_lookup = stack.enter_context(patch.object(router, 'windows_system_proxy', return_value=system))
                        stack.enter_context(patch.object(router, 'candidates', return_value=[dep]))
                        stack.enter_context(patch.object(router, 'choose_credential', return_value=('test-key', 0)))
                        stack.enter_context(patch.object(router, 'record_result'))
                        stack.enter_context(patch.object(router, 'save_event'))
                        stack.enter_context(patch.object(router, 'require_client_key'))
                        stack.enter_context(patch.object(router.httpx, 'AsyncClient', side_effect=make_client))
                        if mode == 'models':
                            result = await client.post('/api/deployments/test-connection', json={
                                **dep, 'api_key': 'test-key'})
                            self.assertEqual(result.json()['models'], ['test-model'])
                        else:
                            result = await client.post('/v1/chat/completions', json={
                                'model': 'test', 'messages': [{'role': 'user', 'content': 'hello'}], 'stream': mode == 'stream'})
                            self.assertIn('ok', result.text)
                        self.assertEqual(result.status_code, 200, result.text)
                        self.assertEqual(len(received_options), 1)
                        self.assertEqual(received_options[0]['proxy'], expected_proxy)
                        self.assertIs(received_options[0]['trust_env'], expected_env)
                        if not enabled or custom.strip():
                            system_lookup.assert_not_called()
                        else:
                            system_lookup.assert_called_once()

    async def test_nonstream_proxy_policy(self):
        await self.check_requests('chat')

    async def test_stream_proxy_policy(self):
        await self.check_requests('stream')

    async def test_connection_test_and_models_proxy_policy(self):
        await self.check_requests('models')


if __name__ == '__main__':
    unittest.main()
