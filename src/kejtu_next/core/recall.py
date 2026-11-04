"""Pure original-text segmentation and old companion keyword/rank semantics."""
from array import array
import math
import re
import struct
import sys

DIMENSIONS = 768
MODEL = 'embeddinggemma-2:270m-bf16-text'
MODEL_DIGEST = 'afeb999480b2a5e48b590fc0583f8cbc7d206b74156feaead3a7840b81719295'
CONTEXT_TOKENS = 8192
# Space identity includes the encoder, dimensions and input-format policy.
CONTRACT = 'companion-hybrid-v2:' + MODEL_DIGEST + ':768:search-document-v1'

class EmbeddingNotReady(ValueError):
    """Cold/evicted local encoder: keyword recall remains available."""

def query_text(query):
    return 'task: search result | query: '+query

def document_text(body, title=''):
    return 'title: '+(title or 'none')+' | text: '+body

STOP = frozenset('a an the is was are were i you my your me what which when how do did does have has had to of in on and or it that this about remember just now know tell please can could would should said say'.split())
CONTEXT_STOP = STOP | frozenset('''we us our they them those these with for from at as be been being
but so also then than very still again more some any all there here yes no okay ok well really
something anything today yesterday continue thanks thank hello hi lol haha und der die das ein
eine ist sind war ich du wir ihr sie es im am zu von mit auf für den dem des nicht noch mal schon
bitte danke ja nein weiter'''.split())
REFERENTIAL = re.compile(r'\b(it|that|this|those|these|they|same|other|again|continue|'
                         r'back|resume|earlier|previous|das|dies|damit|weiter|wieder|vorhin)\b', re.I)

def needs_context(question):
    terms = {w for w in re.findall(r'[^\W_]+', question.casefold()) if len(w)>1 and w not in CONTEXT_STOP}
    return len(question.split())<14 and ((REFERENTIAL.search(question) and bool(terms)) or
        question.strip().casefold().rstrip('.!?') in {'yes','sure','okay','ok','ja','continue','go on','weiter'})

def query_for(question, previous=()):
    """Old literal continuation expansion; no model-generated search wording."""
    query = question if len(question)<=2000 else question[:500]+'\n'+question[-1499:]
    if previous and needs_context(question):
        query = question[:700]+'\n'+'\n'.join(body[:600] for body in previous[:2])
    return query[:2000]

def keywords(query):
    words = list(dict.fromkeys(w.casefold() for w in re.findall(r'[^\W_]+', query, re.UNICODE)
                              if w.casefold() not in STOP))
    if len(words) > 24:
        words = words[:12] + words[-12:]
    return ' OR '.join('"' + word + '"' for word in words)

def segments(body, size=1200, overlap=160):
    """Same character boundaries as the old index; expose exact UTF-8 offsets."""
    start = 0
    byte_start = 0
    while start < len(body):
        end = min(start + size, len(body))
        if end < len(body):
            boundary = body.rfind('\n', start + size // 2, end)
            if boundary < 0:
                boundary = body.rfind(' ', start + size // 2, end)
            if boundary > start:
                end = boundary
        part = body[start:end]
        yield byte_start, part
        if end == len(body):
            break
        next_start = end - overlap
        byte_start += len(body[start:next_start].encode())
        start = next_start

def vector_bytes(values):
    if (not isinstance(values, (list, tuple)) or len(values) != DIMENSIONS
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)
            or not any(values)):
        raise ValueError('Invalid embedding vector')
    norm = math.hypot(*values)
    if not math.isfinite(norm) or norm == 0:
        raise ValueError('Invalid embedding norm')
    return struct.pack('<' + 'f' * DIMENSIONS, *(v / norm for v in values))

def unpack_vector(raw):
    """Validate once into compact float32 storage; derived caches never own text."""
    if len(raw) != 4 * DIMENSIONS:
        raise ValueError('Invalid stored vector')
    values = array('f')
    values.frombytes(raw)
    if sys.byteorder != 'little':
        values.byteswap()
    if not all(math.isfinite(v) for v in values):
        raise ValueError('Invalid stored vector')
    return values


def similarity(left, right):
    a, b = unpack_vector(left), unpack_vector(right)
    return sum(x * y for x, y in zip(a, b))

def fuse(keyword_rows, semantic_rows, *, historical=False):
    """One vote per record per route (per revision in explicit historical mode)."""
    candidates = {}
    for route, rows in (('keyword', keyword_rows), ('semantic', semantic_rows)):
        seen = set()
        for row in rows:
            identity = (row['kind'], row['source_id'] if historical else row['record_id'])
            if identity in seen:
                continue
            seen.add(identity)
            item = candidates.setdefault(identity, dict(row=row, score=0., routes=[]))
            item['score'] += 1 / (60 + len(seen))
            item['routes'].append(route)
    return sorted(candidates.values(), key=lambda item: (-item['score'], item['row']['record_id'], item['row']['source_id']))
