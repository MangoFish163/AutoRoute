import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

_data = tempfile.TemporaryDirectory()
os.environ['LOCAL_MODEL_ROUTER_HOME'] = _data.name
import app as router
from responses_adapter import to_responses, responses_to_chat, responses_chat_stream, anthropic_for_responses

Client = httpx.AsyncClient
BASE = 'https://ark.cn-beijing.volces.com/api/v3'
USAGE = {'input_tokens': 1348, 'output_tokens': 1294, 'total_tokens': 2642,
         'input_tokens_details': {'cached_tokens': 1348}, 'output_tokens_details': {'reasoning_tokens': 867}}
RESULT = {'id': 'resp_test', 'created_at': 1793441661, 'model': 'upstream-model', 'status': 'completed',
          'output': [{'type': 'reasoning', 'summary': [{'type': 'summary_text', 'text': '思考摘要'}], 'encrypted_content': 'opaque'},
                     {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': '这是一张图片。'}]}],
          'usage': USAGE}


class FragmentedStream(httpx.AsyncByteStream):
    def __init__(self, wire):
        self.wire = wire

    async def __aiter__(self):
        for offset in range(0, len(self.wire), 7):
            yield self.wire[offset:offset + 7]


def sse(events):
    return ''.join('event: ' + e['type'] + '\r\ndata: ' + json.dumps(e, ensure_ascii=False) + '\r\n\r\n' for e in events).encode()


class ResponsesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = patch.multiple(router, DB_PATH=Path(self.tmp.name) / 'router.db',
                                    BOOTSTRAP_KEY_PATH=Path(self.tmp.name) / 'client.key')
        self.paths.start()
        router.init_db()

    def tearDown(self):
        self.paths.stop()
        self.tmp.cleanup()

    async def request(self, provider='responses', full=False, stream=False, inbound='openai', events=None):
        dep = dict(id='test', name='豆包', provider=provider, upstream_model='doubao-seed-evolving', api_keys=['test-key'],
                   base_url=BASE + '/responses' if full else BASE, full_url=full, timeout_seconds=5,
                   use_system_proxy=False, proxy_url='')
        def upstream(req):
            self.assertEqual(str(req.url), BASE + '/responses')
            self.assertEqual(req.headers['authorization'], 'Bearer test-key')
            body = json.loads(req.content)
            self.assertEqual(body['model'], 'doubao-seed-evolving')
            self.assertNotIn('messages', body)
            self.assertNotIn('stream_options', body)
            self.assertNotIn('max_tokens', body)
            self.assertEqual(body['input'][0]['content'][0], {'type': 'input_image', 'image_url': 'https://example.test/image.png'})
            self.assertEqual(body['input'][0]['content'][1], {'type': 'input_text', 'text': '你看见了什么？'})
            if stream:
                return httpx.Response(200, stream=FragmentedStream(sse(events)), headers={'content-type': 'text/event-stream'})
            return httpx.Response(200, json=RESULT)
        parts = [{'type': 'image_url', 'image_url': {'url': 'https://example.test/image.png'}}, {'type': 'text', 'text': '你看见了什么？'}]
        if inbound == 'anthropic':
            parts[0] = {'type': 'image', 'source': {'type': 'url', 'url': 'https://example.test/image.png'}}
        async with Client(transport=httpx.ASGITransport(app=router.app), base_url='http://test') as client:
            with patch.object(router, 'candidates', return_value=[dep]), patch.object(router, 'choose_credential', return_value=('test-key', 0)), patch.object(router, 'record_result'), patch.object(router, 'require_client_key'), patch.object(router.httpx, 'AsyncClient', side_effect=lambda **_: Client(transport=httpx.MockTransport(upstream))):
                response = await client.post('/v1/messages' if inbound == 'anthropic' else '/v1/chat/completions', json={
                    'model': 'local-alias', 'messages': [{'role': 'user', 'content': parts}], 'max_tokens': 512,
                    'stream': stream, 'stream_options': {'include_usage': True}})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers['x-local-router-deployment'], '%E8%B1%86%E5%8C%85')
            event = (await client.get('/api/events')).json()[0]
        self.assertEqual(event['usage'], USAGE)
        self.assertEqual((event['input_tokens'], event['output_tokens'], event['total_tokens']), (1348, 1294, 2642))
        return response

    async def test_nonstream_image_reasoning_and_full_usage(self):
        data = (await self.request()).json()
        self.assertEqual(data['model'], 'local-alias')
        self.assertEqual(data['choices'][0]['message']['content'], '这是一张图片。')
        self.assertEqual(data['choices'][0]['message']['reasoning_content'], '思考摘要')
        self.assertNotIn('encrypted_content', json.dumps(data))
        self.assertEqual(data['usage']['prompt_tokens_details']['cached_tokens'], 1348)
        self.assertEqual(data['usage']['completion_tokens_details']['reasoning_tokens'], 867)

    async def test_existing_openai_full_responses_url_is_auto_detected(self):
        await self.request(provider='openai', full=True)

    async def test_anthropic_image_nonstream(self):
        data = (await self.request(inbound='anthropic')).json()
        self.assertEqual(data['content'][0]['text'], '这是一张图片。')
        self.assertEqual(data['usage']['input_tokens'], 1348)

    async def test_fragmented_stream_translates_text_reasoning_tools_and_usage(self):
        events = [
            {'type': 'response.created', 'response': {'id': 'resp_test', 'created_at': 1793441661}},
            {'type': 'response.reasoning_summary_text.delta', 'delta': '思考摘要'},
            {'type': 'response.output_text.delta', 'delta': '这是一张图片。'},
            {'type': 'response.output_item.added', 'output_index': 2, 'item': {'type': 'function_call', 'id': 'fc_1', 'call_id': 'call_1', 'name': 'weather', 'arguments': ''}},
            {'type': 'response.function_call_arguments.delta', 'output_index': 2, 'delta': '{"city":"北京"}'},
            {'type': 'response.completed', 'response': RESULT},
        ]
        response = await self.request(stream=True, events=events)
        frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ') and line != 'data: [DONE]']
        self.assertTrue(all(f['model'] == 'local-alias' for f in frames))
        deltas = [f['choices'][0]['delta'] for f in frames if f['choices']]
        self.assertEqual(''.join(d.get('content', '') for d in deltas), '这是一张图片。')
        self.assertEqual(''.join(d.get('reasoning_content', '') for d in deltas), '思考摘要')
        self.assertEqual([d['tool_calls'][0]['id'] for d in deltas if d.get('tool_calls') and 'id' in d['tool_calls'][0]], ['call_1'])
        self.assertEqual(frames[-2]['choices'][0]['finish_reason'], 'tool_calls')
        self.assertEqual(frames[-1]['usage']['total_tokens'], 2642)
        self.assertTrue(response.text.endswith('data: [DONE]\n\n'))

    def test_endpoint_paths(self):
        for provider, base, full, expected in [
            ('responses', BASE, False, BASE + '/responses'),
            ('openai', BASE + '/responses', False, BASE + '/responses'),
            ('openai', BASE, False, BASE + '/chat/completions'),
            ('openai', 'https://example.test/v1', False, 'https://example.test/v1/chat/completions'),
            ('anthropic', 'https://example.test/v1', False, 'https://example.test/v1/messages'),
            ('responses', BASE + '/responses', True, BASE + '/responses'),
        ]:
            with self.subTest(base=base, provider=provider):
                self.assertEqual(router.endpoint(base, provider, 'openai', full), expected)

    def test_tools_history_limits_and_json_schema(self):
        body = to_responses({'messages': [
            {'role': 'system', 'content': 'Be helpful'},
            {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call_1', 'function': {'name': 'weather', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'call_1', 'content': 'sunny'},
        ], 'tools': [{'type': 'function', 'function': {'name': 'weather', 'parameters': {'type': 'object'}}}],
            'tool_choice': {'type': 'function', 'function': {'name': 'weather'}}, 'max_completion_tokens': 900,
            'response_format': {'type': 'json_schema', 'json_schema': {'name': 'weather', 'schema': {'type': 'object'}}}}, 'upstream')
        self.assertEqual(body['input'][1]['type'], 'function_call')
        self.assertEqual(body['input'][2]['call_id'], 'call_1')
        self.assertEqual(body['tools'][0]['name'], 'weather')
        self.assertEqual(body['tool_choice'], {'type': 'function', 'name': 'weather'})
        self.assertEqual(body['max_output_tokens'], 900)
        self.assertEqual(body['text']['format']['name'], 'weather')

    async def test_stream_failure_and_truncation_are_not_success(self):
        for events in ([{'type': 'response.output_text.delta', 'delta': 'partial'}],
                       [{'type': 'response.failed', 'response': {'error': {'message': 'failed'}}}]):
            response = httpx.Response(200, stream=FragmentedStream(sse(events)))
            with self.assertRaises(ValueError):
                async for _ in responses_chat_stream(response, 'alias', router.StreamUsage()):
                    pass
            await response.aclose()

    def test_incomplete_and_tool_response(self):
        data = responses_to_chat({'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'},
                                  'output': [{'type': 'function_call', 'call_id': 'call_1', 'name': 'weather', 'arguments': '{}'}]}, 'alias')
        self.assertEqual(data['choices'][0]['finish_reason'], 'length')
        self.assertEqual(data['choices'][0]['message']['tool_calls'][0]['id'], 'call_1')

    def test_assistant_history_has_explicit_message_type(self):
        body = to_responses({'messages': [{'role': 'assistant', 'content': [{'type': 'text', 'text': 'Earlier answer'}]}]}, 'model')
        self.assertEqual(body['input'], [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Earlier answer'}]}])

    def test_anthropic_tools_system_and_base64_image(self):
        chat = anthropic_for_responses({'system': [{'type': 'text', 'text': 'Be helpful'}], 'messages': [
            {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'call_1', 'name': 'weather', 'input': {'city': '北京'}}]},
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'call_1', 'content': 'sunny'},
                                          {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'YWJj'}}]},
        ], 'tools': [{'name': 'weather', 'input_schema': {'type': 'object'}}],
            'tool_choice': {'type': 'tool', 'name': 'weather'}}, 'model')
        body = to_responses(chat, 'model')
        self.assertEqual([i.get('type') for i in body['input']], ['message', 'function_call', 'function_call_output', 'message'])
        self.assertEqual(body['input'][-1]['content'][0]['image_url'], 'data:image/png;base64,YWJj')
        self.assertEqual(body['tool_choice']['name'], 'weather')

    async def test_incomplete_stream_and_optional_usage(self):
        events = [{'type': 'response.created', 'response': {'id': 'resp_test'}},
                  {'type': 'response.incomplete', 'response': {**RESULT, 'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'}}}]
        response = httpx.Response(200, stream=FragmentedStream(sse(events)))
        collector = router.StreamUsage()
        wire = b''.join([part async for part in responses_chat_stream(response, 'alias', collector)])
        self.assertIn(b'"finish_reason": "length"', wire)
        self.assertNotIn(b'"usage"', wire)
        self.assertEqual(collector.usage, USAGE)
        await response.aclose()

    async def test_crud_accepts_responses_and_models_url_strips_endpoint(self):
        async with Client(transport=httpx.ASGITransport(app=router.app), base_url='http://test') as client:
            created = await client.post('/api/deployments', json={'name': '豆包', 'alias': 'doubao', 'provider': 'responses',
                                      'base_url': BASE, 'upstream_model': 'doubao-seed-evolving', 'api_keys': ['test-key']})
            self.assertEqual(created.status_code, 200, created.text)
            dep = created.json()
            updated = await client.put('/api/deployments/' + dep['id'], json={**dep, 'api_keys': []})
            self.assertEqual(updated.status_code, 200, updated.text)
            def upstream(req):
                self.assertEqual(str(req.url), BASE + '/models')
                return httpx.Response(200, json={'data': [{'id': 'doubao-seed-evolving'}]})
            with patch.object(router.httpx, 'AsyncClient', side_effect=lambda **_: Client(transport=httpx.MockTransport(upstream))):
                tested = await client.post('/api/deployments/test-connection', json={'provider': 'responses', 'base_url': BASE + '/responses',
                                            'api_key': 'test-key', 'use_system_proxy': False})
            self.assertEqual(tested.status_code, 200, tested.text)
            self.assertEqual(tested.json()['models'], ['doubao-seed-evolving'])


if __name__ == '__main__':
    unittest.main()
