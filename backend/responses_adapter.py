"""Adapt Chat Completions clients to an upstream Responses API (including Ark)."""

import json
import time
import uuid
from urllib.parse import urlsplit


def uses_responses(dep):
    return dep.get('provider') == 'responses' or (
        dep.get('provider', 'openai') == 'openai'
        and urlsplit(dep.get('base_url', '')).path.rstrip('/').endswith('/responses')
    )


def anthropic_for_responses(payload, model):
    def content_blocks(content):
        if not isinstance(content, list):
            return content
        blocks = []
        for part in content:
            if part.get('type') == 'text':
                blocks.append({'type': 'text', 'text': part['text']})
            elif part.get('type') == 'image':
                source = part['source']
                url = source.get('url') if source['type'] == 'url' else f"data:{source['media_type']};base64,{source['data']}"
                blocks.append({'type': 'image_url', 'image_url': {'url': url}})
            elif part.get('type') not in ('tool_use', 'tool_result'):
                raise ValueError(f"Unsupported Anthropic content type for Responses: {part.get('type')}")
        return blocks

    messages = []
    if payload.get('system'):
        messages.append({'role': 'system', 'content': content_blocks(payload['system'])})
    for msg in payload.get('messages', []):
        content = msg.get('content', '')
        message = {'role': msg['role'], 'content': content_blocks(content)}
        if isinstance(content, list):
            calls = [{'id': part['id'], 'type': 'function', 'function': {'name': part['name'], 'arguments': json.dumps(part['input'])}}
                     for part in content if part.get('type') == 'tool_use']
            if calls:
                message['tool_calls'] = calls
            results = [part for part in content if part.get('type') == 'tool_result']
            for part in results:
                messages.append({'role': 'tool', 'tool_call_id': part['tool_use_id'], 'content': part.get('content', '')})
            if not message['content'] and not calls:
                continue
            if not message['content']:
                message['content'] = None
        messages.append(message)
    out = {key: payload[key] for key in ('max_tokens', 'stream', 'temperature', 'top_p') if key in payload}
    out.update(model=model, messages=messages)
    if 'tools' in payload:
        out['tools'] = [{'type': 'function', 'function': {'name': t['name'], 'description': t.get('description', ''),
                         'parameters': t.get('input_schema', {})}} for t in payload['tools']]
    if 'tool_choice' in payload:
        choice = payload['tool_choice']
        out['tool_choice'] = {'type': 'function', 'function': {'name': choice['name']}} if choice['type'] == 'tool' else {'any': 'required'}.get(choice['type'], choice['type'])
    return out


def to_responses(payload, model):
    if payload.get('n', 1) != 1:
        raise ValueError('Responses upstream supports only n=1')
    items = []
    for msg in payload.get('messages', []):
        role, content = msg.get('role', 'user'), msg.get('content')
        if role == 'tool':
            items.append({'type': 'function_call_output', 'call_id': msg['tool_call_id'],
                          'output': content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)})
            continue
        if content is not None:
            if isinstance(content, list):
                blocks = []
                for part in content:
                    kind = part.get('type')
                    if kind == 'text':
                        blocks.append({'type': 'output_text' if role == 'assistant' else 'input_text', 'text': part['text']})
                    elif kind == 'image_url':
                        image = part['image_url']
                        block = {'type': 'input_image', 'image_url': image if isinstance(image, str) else image['url']}
                        if isinstance(image, dict) and image.get('detail'):
                            block['detail'] = image['detail']
                        blocks.append(block)
                    else:
                        raise ValueError(f'Unsupported Chat content type for Responses: {kind}')
                content = blocks
            # Ark requires an explicit message discriminator for assistant output_text.
            items.append({'type': 'message', 'role': role, 'content': content})
        for call in msg.get('tool_calls', []):
            fn = call['function']
            items.append({'type': 'function_call', 'call_id': call['id'], 'name': fn['name'], 'arguments': fn['arguments']})
    out = {'model': model, 'input': items, 'stream': bool(payload.get('stream', False))}
    for field in ('temperature', 'top_p', 'metadata', 'parallel_tool_calls', 'thinking', 'reasoning', 'store'):
        if field in payload:
            out[field] = payload[field]
    limit = payload.get('max_completion_tokens', payload.get('max_tokens'))
    if limit is not None:
        out['max_output_tokens'] = limit
    if 'tools' in payload:
        out['tools'] = [dict(type='function', **tool['function']) if tool.get('type') == 'function' else tool
                        for tool in payload['tools']]
    if 'tool_choice' in payload:
        choice = payload['tool_choice']
        out['tool_choice'] = {'type': 'function', 'name': choice['function']['name']} if isinstance(choice, dict) and 'function' in choice else choice
    if 'response_format' in payload:
        fmt = payload['response_format']
        if fmt.get('type') == 'json_schema':
            fmt = {'type': 'json_schema', **fmt['json_schema']}
        out['text'] = {'format': fmt}
    return out


def chat_usage(usage):
    result = {'prompt_tokens': usage.get('input_tokens', 0), 'completion_tokens': usage.get('output_tokens', 0),
              'total_tokens': usage.get('total_tokens', usage.get('input_tokens', 0) + usage.get('output_tokens', 0))}
    for source, target in (('input_tokens_details', 'prompt_tokens_details'), ('output_tokens_details', 'completion_tokens_details')):
        if source in usage:
            result[target] = usage[source]
    return result


def finish_reason(data, calls=False):
    if data.get('status') == 'incomplete':
        return 'length' if (data.get('incomplete_details') or {}).get('reason') == 'max_output_tokens' else 'content_filter'
    return 'tool_calls' if calls else 'stop'


def responses_to_chat(data, alias):
    if data.get('error') or data.get('status') in ('failed', 'cancelled'):
        raise ValueError('Responses upstream failed: ' + str(data.get('error') or data.get('status')))
    texts, reasoning, calls = [], [], []
    for item in data.get('output', []):
        if item.get('type') == 'message':
            texts.extend(part['text'] for part in item.get('content', []) if part.get('type') == 'output_text')
        elif item.get('type') == 'reasoning':
            reasoning.extend(part['text'] for part in item.get('summary', []) if part.get('type') == 'summary_text')
        elif item.get('type') == 'function_call':
            calls.append({'id': item['call_id'], 'type': 'function', 'function': {'name': item['name'], 'arguments': item['arguments']}})
    message = {'role': 'assistant', 'content': ''.join(texts) or None}
    if reasoning:
        message['reasoning_content'] = ''.join(reasoning)
    if calls:
        message['tool_calls'] = calls
    result = {'id': data.get('id', 'chatcmpl-' + uuid.uuid4().hex), 'object': 'chat.completion',
              'created': data.get('created_at', int(time.time())), 'model': alias,
              'choices': [{'index': 0, 'message': message, 'finish_reason': finish_reason(data, bool(calls))}]}
    if isinstance(data.get('usage'), dict):
        result['usage'] = chat_usage(data['usage'])
    return result


async def responses_chat_stream(response, alias, collector, include_usage=False):
    """Read SSE by lines so split UTF-8 characters and network chunks are safe."""
    response_id, created = 'chatcmpl-' + uuid.uuid4().hex, int(time.time())
    started, completed = False, False
    calls = {}

    def chunk(delta, finish=None, usage=None):
        data = {'id': response_id, 'object': 'chat.completion.chunk', 'created': created, 'model': alias,
                'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
        if usage is not None:
            data.update(choices=[], usage=chat_usage(usage))
        return ('data: ' + json.dumps(data, ensure_ascii=False) + '\n\n').encode()

    async def events():
        lines = []
        async for line in response.aiter_lines():
            if not line:
                if lines:
                    yield json.loads('\n'.join(lines))
                    lines = []
            elif line.startswith('data:'):
                value = line[5:].lstrip()
                if value != '[DONE]':
                    lines.append(value)
        if lines:
            yield json.loads('\n'.join(lines))

    async for event in events():
        collector.feed(('data: ' + json.dumps(event) + '\n').encode())
        kind = event.get('type', '')
        data = event.get('response') or {}
        if data.get('id'):
            response_id = data['id']
            created = data.get('created_at', created)
        if kind in ('error', 'response.failed', 'response.cancelled'):
            raise ValueError('Responses stream failed: ' + str(event.get('error') or data.get('error') or event.get('message') or kind))
        if not started:
            yield chunk({'role': 'assistant'})
            started = True
        if kind == 'response.output_text.delta':
            yield chunk({'content': event.get('delta', '')})
        elif kind in ('response.reasoning_summary_text.delta', 'response.reasoning_text.delta'):
            yield chunk({'reasoning_content': event.get('delta', '')})
        elif kind == 'response.output_item.added' and event.get('item', {}).get('type') == 'function_call':
            item = event['item']
            index = len(calls)
            calls[event['output_index']] = index
            yield chunk({'tool_calls': [{'index': index, 'id': item['call_id'], 'type': 'function',
                                        'function': {'name': item['name'], 'arguments': item.get('arguments', '')}}]})
        elif kind == 'response.function_call_arguments.delta':
            index = calls[event['output_index']]
            yield chunk({'tool_calls': [{'index': index, 'function': {'arguments': event.get('delta', '')}}]})
        elif kind in ('response.completed', 'response.incomplete'):
            if data.get('error'):
                raise ValueError('Responses stream failed: ' + str(data['error']))
            yield chunk({}, finish_reason(data, bool(calls)))
            if include_usage and isinstance(data.get('usage'), dict):
                yield chunk({}, usage=data['usage'])
            yield b'data: [DONE]\n\n'
            completed = True
            break
    if not completed:
        raise ValueError('Responses stream ended before a terminal response event')
