import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from types import SimpleNamespace

import httpx

_data = tempfile.TemporaryDirectory()
os.environ['LOCAL_MODEL_ROUTER_HOME'] = _data.name
import app as router

Client = httpx.AsyncClient
DEEPSEEK = {'prompt_tokens': 31, 'completion_tokens': 38, 'total_tokens': 69,
            'prompt_tokens_details': {'cached_tokens': 0},
            'completion_tokens_details': {'reasoning_tokens': 28},
            'prompt_cache_hit_tokens': 0, 'prompt_cache_miss_tokens': 31,
            'provider_extension': {'future_field': [1, '保留']}}


class UsageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(router, 'DB_PATH', Path(self.tmp.name) / 'test.db')
        self.db.start()
        router.init_db()

    def tearDown(self):
        self.db.stop()
        self.tmp.cleanup()

    async def request(self, provider='openai', inbound='openai', stream=False, usage=DEEPSEEK):
        dep = dict(id='test', name='test', provider=provider, upstream_model='test',
                   api_keys=['test-key'], base_url='https://mock.invalid', timeout_seconds=5,
                   use_system_proxy=False, proxy_url='')
        if stream:
            if provider == 'anthropic':
                frames = [{'type': 'message_start', 'message': {'usage': {'input_tokens': 31, 'output_tokens': 0, 'cache_read_input_tokens': 5}}},
                          {'type': 'message_delta', 'usage': {'output_tokens': 38}}]
            else:
                frames = [{'usage': None}, {'usage': usage}]
            wire = b''.join(b'data: ' + json.dumps(f).encode() + b'\r\n\r\n' for f in frames) + b'data: [DONE]\n\n'
            response = lambda _: httpx.Response(200, content=wire, headers={'content-type': 'text/event-stream'})
        else:
            body = {'id': 'test', 'usage': usage, 'choices': [{'message': {'content': 'ok'}}], 'content': [{'type': 'text', 'text': 'ok'}]}
            response = lambda _: httpx.Response(200, json=body)
        async with Client(transport=httpx.ASGITransport(app=router.app), base_url='http://test') as client:
            with patch.object(router, 'candidates', return_value=[dep]), patch.object(router, 'choose_credential', return_value=('test-key', 0)), patch.object(router, 'record_result'), patch.object(router, 'require_client_key'), patch.object(router.httpx, 'AsyncClient', side_effect=lambda **_: Client(transport=httpx.MockTransport(response))):
                result = await client.post('/v1/messages' if inbound == 'anthropic' else '/v1/chat/completions', json={'model': 'test', 'messages': [{'role': 'user', 'content': 'hello'}], 'stream': stream})
                self.assertEqual(result.status_code, 200, result.text)
            events = (await client.get('/api/events')).json()
        self.assertEqual(len(events), 1)
        return events[0]

    async def test_non_stream_keeps_full_provider_usage(self):
        event = await self.request()
        self.assertEqual(event['usage'], DEEPSEEK)
        self.assertEqual((event['input_tokens'], event['output_tokens'], event['total_tokens']), (31, 38, 69))

    async def test_native_window_can_access_backend(self):
        async with Client(transport=httpx.ASGITransport(app=router.app), base_url='http://test') as client:
            response = await client.options('/api/events', headers={'Origin': 'http://tauri.localhost', 'Access-Control-Request-Method': 'GET', 'Access-Control-Request-Headers': 'content-type'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['access-control-allow-origin'], 'http://tauri.localhost')

    async def test_cross_protocol_keeps_original_usage(self):
        event = await self.request(inbound='anthropic')
        self.assertEqual(event['usage'], DEEPSEEK)

    async def test_reverse_cross_protocol(self):
        usage = {'input_tokens': 31, 'output_tokens': 38, 'cache_creation_input_tokens': 5}
        event = await self.request(provider='anthropic', usage=usage)
        self.assertEqual(event['usage'], usage)
        self.assertEqual(event['total_tokens'], 69)

    async def test_openai_stream_keeps_details(self):
        event = await self.request(stream=True)
        self.assertEqual(event['usage'], DEEPSEEK)
        self.assertEqual(event['usage_events'], [DEEPSEEK])

    async def test_anthropic_stream_merges_cumulative_usage(self):
        event = await self.request(provider='anthropic', inbound='anthropic', stream=True)
        self.assertEqual(event['usage'], {'input_tokens': 31, 'output_tokens': 38, 'cache_read_input_tokens': 5})
        self.assertEqual(event['total_tokens'], 69)
        self.assertEqual(len(event['usage_events']), 2)

    def test_fragmented_stream_and_unterminated_last_line(self):
        collector = router.StreamUsage()
        wire = b'data: {"usage":{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}}'
        for byte in wire:
            collector.feed(bytes([byte]))
        collector.feed(b'', final=True)
        self.assertEqual(router.token_usage(collector.usage), (0, 0, 0))

    def test_supplier_is_service_brand_and_snapshot_is_immutable(self):
        dep = {'id':'test', 'name':'DS4', 'provider':'openai', 'base_url':'https://api.deepseek.com/v1'}
        router.save_event('supplier', 'openai', 'test', dep, 'success', 200, 120000, None, None, [])
        with router.connect() as db:
            db.execute("INSERT INTO settings(key,value) VALUES('provider_presets',?)", (json.dumps({'changed':{'name':'Changed', 'base_url':dep['base_url']}}),))
        event = router.events()[0]
        self.assertEqual(event['supplier_name'], 'DeepSeek')
        self.assertEqual(event['supplier_source'], 'snapshot')
        self.assertEqual(event['latency_ms'], 120000)

    def test_custom_supplier_and_failed_attempt_snapshot(self):
        dep = {'supplier_id':'vendor-1', 'supplier_name':'自定义供应商', 'base_url':'https://mock.invalid'}
        supplier = router.supplier_snapshot(dep)
        router.save_event('failure', 'openai', 'test', None, 'error', 502, 120000, None, None, [supplier])
        event = router.events()[0]
        self.assertEqual(event['supplier_name'], '自定义供应商')
        self.assertEqual(event['attempts'][0]['supplier_id'], 'vendor-1')

    async def test_retry_duration_includes_all_attempts(self):
        dep = dict(id='test', name='test', provider='openai', upstream_model='test', api_keys=['key'],
                   base_url='https://mock.invalid', timeout_seconds=5, use_system_proxy=False, proxy_url='')
        responses = iter([httpx.Response(500), httpx.Response(200, json={'usage':DEEPSEEK})])
        with patch.object(router, 'candidates', return_value=[dep, dep]), patch.object(router, 'choose_credential', return_value=('key',0)), patch.object(router, 'record_result'), patch.object(router, 'cooldown_credential'), patch.object(router, 'time', SimpleNamespace(perf_counter=Mock(side_effect=[10,70,130]))), patch.object(router.httpx, 'AsyncClient', side_effect=lambda **_: Client(transport=httpx.MockTransport(lambda _: next(responses)))):
            result = await router.forward({'model':'test'}, 'openai', 'retry')
        self.assertEqual(result[4], 120000)
        self.assertEqual(len(result[2]), 2)

    async def test_missing_usage_is_unknown(self):
        event = await self.request(usage=None)
        self.assertIsNone(event['usage'])
        self.assertIsNone(event['input_tokens'])
        self.assertIsNone(event['total_tokens'])

    def test_old_schema_is_migrated_without_inventing_usage(self):
        with router.connect() as db:
            for column in ('input_tokens', 'output_tokens', 'usage', 'usage_events'):
                db.execute(f'ALTER TABLE request_events DROP COLUMN {column}')
        router.init_db()
        router.save_event('old', 'openai', 'test', None, 'success', 200, 1, None, None, [], 195)
        event = router.events()[0]
        self.assertEqual(event['total_tokens'], 195)
        self.assertIsNone(event['usage'])


if __name__ == '__main__':
    unittest.main()
