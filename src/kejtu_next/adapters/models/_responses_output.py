"""Bounded Responses output assembly; completion still belongs to the envelope."""
import json

from ...core.records import Failure
from ._streaming import invalid


def identity(item):
    if not isinstance(item, dict):
        raise invalid()
    kind, ident = item.get('type'), item.get('id')
    if not isinstance(kind, str) or not isinstance(ident, str) or not ident or len(ident) > 256:
        raise invalid()
    # A function's invocation identity must survive added/done/final snapshots.
    extra = tuple(item.get(key) for key in ('call_id', 'name', 'namespace')) if kind == 'function_call' else ()
    if any(value is not None and not isinstance(value, str) for value in extra):
        raise invalid()
    return kind, ident, *extra


class ResponseOutput:
    def __init__(self, maximum):
        self.maximum = maximum
        self.added = {}
        self.done = {}
        self.identities = {}
        self.sizes = {}
        self.size = 0

    def item(self, event, *, complete):
        index, item = event.get('output_index'), event.get('item')
        if type(index) is not int or index < 0 or index >= self.maximum:
            raise invalid()
        # Some streams announce an empty slot and finalize a later index.
        if item is None and not complete:
            return
        key = identity(item)
        previous = self.added.get(index)
        if previous is not None and previous != key:
            raise invalid()
        previous_done = self.done.get(index)
        if previous_done is not None and identity(previous_done) != key:
            raise invalid()
        position = self.identities.get(key[1])
        if position is not None and position != index:
            raise invalid()
        if not complete and (index in self.added or index in self.done):
            raise invalid()
        # Charge both the identities and finalized bodies, not repeated deltas.
        charge = len(json.dumps(item if complete else key, ensure_ascii=False).encode())
        self.size += charge - self.sizes.get(index, 0)
        if self.size > self.maximum:
            raise Failure('response_size', 'OpenAI output items exceeded the inherited byte bound.')
        self.sizes[index] = charge
        self.identities[key[1]] = index
        if complete:
            self.done[index] = item
        else:
            self.added[index] = key

    def completed(self, response):
        output = response.get('output')
        if output is not None and not isinstance(output, list):
            raise invalid()
        if output:
            # A populated terminal snapshot is authoritative, but cannot silently
            # lose/reorder previously announced identities or replace a function.
            positions = {}
            for index, item in enumerate(output):
                key = identity(item)
                if key[1] in positions:
                    raise invalid()
                positions[key[1]] = index, key
            last = -1
            for index in sorted(self.added.keys() | self.done.keys()):
                key = identity(self.done[index]) if index in self.done else self.added[index]
                final_index, final_key = positions.get(key[1], (-1, None))
                if final_key != key or final_index <= last:
                    raise invalid()
                last = final_index
            return response
        # ChatGPT can send output: [] in response.completed after finalizing all
        # items separately. Missing/null output is used by other compact streams.
        # Never reconstruct tools or reasoning from deltas/unfinished snapshots.
        if self.added.keys() - self.done.keys():
            raise invalid()
        return dict(response, output=[self.done[index] for index in sorted(self.done)])
