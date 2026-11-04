"""Read-only attachment views over immutable user messages; no second store.

Prose pages use offsets in their explicit presentation, never in the encoded
original. Original body.read, model input, search indexes and saved bytes remain
unchanged. Image reads recheck scope, source identity and exact original pixels.
"""
from collections import OrderedDict

from ...core.records import Failure
from ...core.reference import exact_page
from ...core.vision import DATA_URL, MAX_IMAGES, inspect_image

# A local rendering IO bound, independent of model context allocation. Larger
# legacy bodies retain their exact body.read path with an explicit UI notice.
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_CACHE_BYTES = 8 * 1024 * 1024


class ImageViews:
    def __init__(self, store):
        self.store = store
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.version = None

    def source(self, conversation_id, turn_id, body_id):
        row = self.store.db.execute('''SELECT a.bytes,a.sha256 FROM turns t
            JOIN artifacts a ON a.id=t.user_id WHERE t.conversation_id=? AND t.id=?
            AND t.user_id=? AND a.kind='user_message' ''',
            (conversation_id, turn_id, body_id)).fetchone()
        if row is None:
            raise Failure('body_unavailable', 'This image message is unavailable in this conversation.')
        if not 0 <= row['bytes'] <= MAX_SOURCE_BYTES:
            raise Failure('image_view_size', 'This legacy message is too large for the image view. Its exact body remains readable.')
        return row

    def manifest(self, conversation_id, turn_id, body_id):
        row = self.source(conversation_id, turn_id, body_id)
        # Even a derived RAM view must not hide edits, removals or corruptions.
        version = (self.store.db.total_changes, self.store.db.execute('PRAGMA data_version').fetchone()[0])
        if version != self.version:
            self.cache.clear()
            self.cache_bytes = 0
            self.version = version
        key = (body_id, row['sha256'])
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        body = self.store._body(body_id)
        if len(body.encode()) != row['bytes']:
            raise Failure('store_integrity', 'Image message extent is invalid.')
        images, prose = [], []
        cursor = byte_cursor = 0
        for match in DATA_URL.finditer(body):
            try:
                image = inspect_image(match[1], match[2])
            except ValueError:
                continue  # Preserve legacy malformed examples as exact prose.
            if len(images) >= MAX_IMAGES:
                raise Failure('image_view_size', 'This legacy message has too many images for the gallery. Its exact body remains readable.')
            prefix = body[cursor:match.start()]
            offset = byte_cursor + len(prefix.encode())
            length = len(match[0].encode())
            images.append(dict(index=len(images),offset=offset,length=length,
                **{k:v for k,v in image.items() if k != 'data'}))
            # withImages owns one separator before each attachment, as does
            # draftImages on the client. Preserve the author's own blank lines.
            prose.append(prefix.removesuffix('\n'))
            cursor = match.end()
            byte_cursor = offset + length
        prose.append(body[cursor:])
        text = ''.join(prose)
        value = dict(text=text,images=images,source_sha256=row['sha256'],source_bytes=row['bytes'])
        size = len(text.encode()) + len(images)*512
        if size <= MAX_CACHE_BYTES:
            while self.cache and (len(self.cache) >= 32 or self.cache_bytes+size > MAX_CACHE_BYTES):
                _, old = self.cache.popitem(last=False)
                self.cache_bytes -= old[1]
            self.cache[key] = (value, size)
            self.cache_bytes += size
        return value, size

    def page(self, conversation_id, turn_id, body_id, offset, maximum):
        value, _ = self.manifest(conversation_id, turn_id, body_id)
        page = exact_page(body_id, value['text'], offset, maximum)
        if page.reason:
            raise Failure('invalid_request', 'Image-view text page is outside a UTF-8 boundary or extent.')
        return dict(body_id=body_id,kind='user_message',sha256=page.sha256,
            total_bytes=page.total_bytes,offset=page.offset,end=page.end,content=page.content,
            complete=page.end==page.total_bytes,images=value['images'],
            source_sha256=value['source_sha256'],source_bytes=value['source_bytes'])

    def image(self, conversation_id, turn_id, body_id, index, source_sha256):
        value, _ = self.manifest(conversation_id, turn_id, body_id)
        if source_sha256 != value['source_sha256']:
            raise Failure('image_changed', 'The image message changed. Reopen it before viewing images.')
        if type(index) is not int or not 0 <= index < len(value['images']):
            raise Failure('image_unavailable', 'The selected image is unavailable in this message.')
        meta = value['images'][index]
        parts = []
        offset, end = meta['offset'], meta['offset']+meta['length']
        while offset < end:
            part = self.store.body_page(conversation_id,turn_id,body_id,offset,min(32768,end-offset))
            if part['sha256'] != source_sha256:
                raise Failure('image_changed', 'The image message changed during reading.')
            parts.append(part['content'])
            offset = part['end']
        line = ''.join(parts)
        match = DATA_URL.fullmatch(line)
        try:
            if match is None:
                raise ValueError()
            image = inspect_image(match[1],match[2])
            if any(image[k] != meta[k] for k in ('sha256','bytes','width','height','mime_type')):
                raise ValueError()
        except ValueError:
            raise Failure('store_integrity', 'The saved image failed identity verification.') from None
        return dict(body_id=body_id,index=index,source_sha256=source_sha256,**image)
