"""Bounded provider stream assembly; only provisional text leaves before validation."""
import json

from ...core.records import Failure, ModelProgress


def invalid():
    return Failure('invalid_response', 'Provider stream was incomplete or malformed.')


def lines(response, cap):
    total = 0
    ceiling = cap * 6 + 65536
    while True:
        line = response.readline(ceiling + 1)
        if not line:
            return
        total += len(line)
        if len(line) > ceiling or total > cap * 64 + 65536:
            raise Failure('response_size', 'Provider stream exceeded its transport bound.')
        try:
            yield line.decode('utf-8')
        except UnicodeError:
            raise invalid() from None


def sse(response, cap):
    data = []
    size = 0
    for line in lines(response, cap):
        line = line.rstrip('\r\n')
        if not line:
            if data:
                yield '\n'.join(data)
                data, size = [], 0
            continue
        if line.startswith(':'):
            continue
        field, _, value = line.partition(':')
        if field == 'data':
            value = value.removeprefix(' ')
            size += len(value.encode())
            if size > cap * 6 + 65536:
                raise Failure('response_size', 'Provider stream event exceeded its byte bound.')
            data.append(value)
        elif field not in ('event', 'id', 'retry'):
            raise invalid()
    if data:
        # A complete final event may close without an extra blank line.
        yield '\n'.join(data)


class Assembly:
    def __init__(self, cap, progress):
        self.cap, self.progress = cap, progress
        self.text, self.reasoning = [], []
        self.size = 0
        self.tools = False
        self.thinking = False

    def charge(self, text):
        if not isinstance(text, str):
            raise invalid()
        self.size += len(text.encode('utf-8'))
        if self.size > self.cap:
            raise Failure('response_size', 'Provider content exceeded the inherited byte bound.')

    def append(self, text=None, reasoning=None):
        if reasoning is not None:
            self.charge(reasoning)
            self.reasoning.append(reasoning)
            if reasoning and not self.thinking and not self.text:
                self.thinking = True
                self.progress(ModelProgress('phase', 'thinking'))
        if text is not None:
            self.charge(text)
            self.text.append(text)
            if text and not self.tools:
                self.progress(ModelProgress('text', text))

    def tool(self):
        if not self.tools:
            self.tools = True
            # Public preamble is still useful while tools run. Only an actual
            # rejection retracts it; native dispatch waits for the full Reply.
            self.progress(ModelProgress('phase', 'tools'))

    def encoded(self, result):
        body = json.dumps(result, ensure_ascii=False).encode('utf-8')
        if len(body) > self.cap:
            raise Failure('response_size', 'Provider response exceeded the inherited byte bound.')
        return body


def chat_completion_stream(response, packet, progress):
    assembly = Assembly(packet['response_bytes'], progress)
    calls = {}
    finish = None
    usage = None
    identity = None
    try:
        for raw in sse(response, packet['response_bytes']):
            if raw == '[DONE]':
                if finish is None:
                    raise invalid()
                if set(calls) != set(range(len(calls))):
                    raise invalid()
                message = {'role': 'assistant', 'content': ''.join(assembly.text),
                           'reasoning_content': ''.join(assembly.reasoning)}
                if calls:
                    message['tool_calls'] = [calls[index] for index in sorted(calls)]
                return assembly.encoded({'object': 'chat.completion', 'choices': [
                    {'index': 0, 'finish_reason': finish, 'message': message}], 'usage': usage})
            event = json.loads(raw)
            if not isinstance(event, dict) or event.get('object') != 'chat.completion.chunk':
                raise invalid()
            ident = event.get('id')
            if not isinstance(ident, str) or not ident or identity is not None and ident != identity:
                raise invalid()
            identity = ident
            if event.get('usage') is not None:
                if usage is not None or not isinstance(event['usage'], dict):
                    raise invalid()
                usage = event['usage']
            choices = event.get('choices')
            if not isinstance(choices, list) or len(choices) > 1:
                raise invalid()
            if not choices:
                if finish is None or event.get('usage') is None:
                    raise invalid()
                continue
            choice, = choices
            if not isinstance(choice, dict) or type(choice.get('index')) is not int or choice['index'] != 0:
                raise invalid()
            delta = choice.get('delta')
            if not isinstance(delta, dict) or finish is not None:
                raise invalid()
            if delta.get('role', 'assistant') != 'assistant' or delta.get('function_call') or delta.get('refusal'):
                raise invalid()
            tool_chunks = delta.get('tool_calls') or []
            if not isinstance(tool_chunks, list):
                raise invalid()
            if tool_chunks:
                assembly.tool()
            assembly.append(delta.get('content'), delta.get('reasoning_content'))
            for fragment in tool_chunks:
                if not isinstance(fragment, dict):
                    raise invalid()
                index = fragment.get('index')
                if type(index) is not int or not 0 <= index < packet.get('operation_limit', 1):
                    raise invalid()
                call = calls.setdefault(index, {'id': '', 'type': 'function',
                                                'function': {'name': '', 'arguments': ''}})
                if fragment.get('type', 'function') != 'function':
                    raise invalid()
                if fragment.get('id') is not None:
                    assembly.charge(fragment['id'])
                    call['id'] += fragment['id']
                function = fragment.get('function', {})
                if not isinstance(function, dict) or set(function) - {'name', 'arguments'}:
                    raise invalid()
                for key, value in function.items():
                    if value is not None:
                        assembly.charge(value)
                        call['function'][key] += value
            if choice.get('finish_reason') is not None:
                finish = choice['finish_reason']
                if not isinstance(finish, str):
                    raise invalid()
                progress(ModelProgress('phase', 'finishing'))
    except (ValueError, TypeError, UnicodeError, KeyError):
        raise invalid() from None
    raise invalid()


def ollama_stream(response, packet, progress):
    assembly = Assembly(packet['response_bytes'], progress)
    calls = []
    try:
        for line in lines(response, packet['response_bytes']):
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict) or event.get('error'):
                raise invalid()
            message = event.get('message')
            if not isinstance(message, dict) or message.get('role') != 'assistant':
                raise invalid()
            pieces = message.get('tool_calls') or []
            if not isinstance(pieces, list):
                raise invalid()
            if pieces:
                assembly.tool()
                assembly.charge(json.dumps(pieces, ensure_ascii=False))
                calls.extend(pieces)
                if len(calls) > packet.get('operation_limit', 1):
                    raise invalid()
            assembly.append(message.get('content'), message.get('thinking'))
            if event.get('done') is True:
                result = dict(event)
                result['message'] = {'role': 'assistant', 'content': ''.join(assembly.text),
                                     'thinking': ''.join(assembly.reasoning)}
                if calls:
                    result['message']['tool_calls'] = calls
                progress(ModelProgress('phase', 'finishing'))
                return assembly.encoded(result)
            if event.get('done') is not False:
                raise invalid()
    except (ValueError, TypeError, UnicodeError, KeyError):
        raise invalid() from None
    raise invalid()
