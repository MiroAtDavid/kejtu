"""Bounded inline image projection shared by admission and provider transport.

Exact user text and MCP originals remain with their existing storage owners.
Only explicit user data URLs and application-produced MCP visual envelopes are
projected. No URL fetching, filesystem reads or authority inferred from prose.
"""
import base64
import hashlib
import json
import re
import struct
import zlib

MAX_IMAGE_BYTES = 1_400_000  # Fits the existing 2 MB normalized MCP receipt.
MAX_IMAGES = 32
MAX_REQUEST_IMAGES = 600
# This ceiling also works for the provider's 15-or-more-image requests.
MAX_SIDE = 4096
IMAGE_TOKENS = 1024 + 64  # DeepSeek's documented image ceiling plus framing.
ENCODING = 'MCP visual result v1'
DATA_URL = re.compile(r'(?m)^data:(image/(?:png|jpeg));base64,([A-Za-z0-9+/=]+)[ \t]*\r?$')
IMAGE_LINE = re.compile(r'(?m)^data:image/[^\r\n]*\r?$')
SUPPORTED_MODELS = frozenset(('deepseek-flash', 'deepseek-v4-flash', 'deepseek-v4-flash-vision-exp'))
OPENAI_MODELS = frozenset(('gpt-6.1-sol', 'gpt-6-astra', 'gpt-6-luna'))


def supported(provider, model):
    return (provider == 'deepseek' and model in SUPPORTED_MODELS
            or provider in ('openai', 'chatgpt') and model in OPENAI_MODELS)


def image_token_bound(image, provider=None):
    if provider not in ('openai', 'chatgpt'):
        return IMAGE_TOKENS
    # A development bound, not billing: count unresized 32px patches with a
    # conservative 3x allowance and framing. Do not reuse DeepSeek's 1024-token
    # ceiling or assume every OpenAI model has Astra's documented multiplier.
    # Explicit high detail can reduce actual use; the existing usage anchor
    # replaces this estimate after the provider returns measured input tokens.
    patches = ((image['width'] + 31) // 32) * ((image['height'] + 31) // 32)
    return max(IMAGE_TOKENS, patches * 3 + 64)


def inspect_image(mime, data):
    """Validate bounded encoding/container identity; never decode raster pixels."""
    if mime not in ('image/png', 'image/jpeg') or not isinstance(data, str) or len(data) > 4*((MAX_IMAGE_BYTES+2)//3):
        raise ValueError('Use a PNG or JPEG image within the inline image size limit.')
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, TypeError):
        raise ValueError('The image is not valid Base64.') from None
    if not raw or len(raw) > MAX_IMAGE_BYTES or base64.b64encode(raw).decode() != data:
        raise ValueError('The image encoding or size is invalid.')
    width = height = 0
    if mime == 'image/png':
        if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('Image data does not match PNG format.')
        offset, first, ended, pixels = 8, True, False, False
        while offset+12 <= len(raw):
            size = int.from_bytes(raw[offset:offset+4]); kind = raw[offset+4:offset+8]
            end = offset+12+size
            if end > len(raw) or zlib.crc32(raw[offset+4:end-4]) & 0xffffffff != int.from_bytes(raw[end-4:end]):
                raise ValueError('PNG chunk integrity failed.')
            if first:
                if kind != b'IHDR' or size != 13:
                    raise ValueError('PNG header is invalid.')
                width,height = struct.unpack('>II',raw[offset+8:offset+16]); first=False
            elif kind == b'IHDR':
                raise ValueError('PNG has repeated headers.')
            pixels |= kind == b'IDAT'
            if kind == b'IEND':
                ended = size == 0 and end == len(raw)
                break
            offset=end
        if not ended or not pixels:
            raise ValueError('PNG is incomplete.')
    else:
        if not raw.startswith(b'\xff\xd8') or not raw.endswith(b'\xff\xd9'):
            raise ValueError('Image data does not match JPEG format.')
        offset=2
        while offset+4 <= len(raw):
            if raw[offset] != 255: break
            while offset < len(raw) and raw[offset] == 255: offset+=1
            if offset >= len(raw): break
            marker=raw[offset]; offset+=1
            if marker in (0xd9,0xda): break
            if marker == 0x01 or 0xd0 <= marker <= 0xd8: continue
            size=int.from_bytes(raw[offset:offset+2]); end=offset+size
            if size < 2 or end > len(raw): break
            if marker in (0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf) and size >= 8:
                height,width=struct.unpack('>HH',raw[offset+3:offset+7]); break
            offset=end
    if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE):
        raise ValueError('Image dimensions are unsupported or its header is invalid.')
    return dict(mime_type=mime, data=data, width=width, height=height,
                sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


def projection(role, content, tool_name=None, *, strict=False):
    """Return visible text and exact images; input originals are never changed."""
    if role == 'user':
        images=[]
        def replace(line):
            match=DATA_URL.fullmatch(line[0])
            try:
                if match is None:
                    raise ValueError('Use a complete Base64 PNG or JPEG image on its own line.')
                image=inspect_image(match[1],match[2])
            except ValueError:
                # Legacy messages may discuss malformed data URLs as prose.
                # New user submissions validate strictly before being saved.
                if strict: raise
                return line[0]
            if len(images) >= MAX_REQUEST_IMAGES:
                raise ValueError('Too many inline images in this request.')
            images.append(image)
            return '[Image '+str(len(images))+': '+image['sha256']+']'
        text=IMAGE_LINE.sub(replace,content)
        return text,images
    if role == 'tool' and tool_name in ('mcp_call','mcp_read','university') and (ENCODING in content or 'Workspace visual result v1' in content):
        try: value=json.loads(content)
        except (ValueError,TypeError): return content,[]
        if not isinstance(value,dict) or (value.get('domain'),value.get('encoding'),tool_name) not in (('mcp',ENCODING,'mcp_call'),('mcp',ENCODING,'mcp_read'),('workspace','Workspace visual result v1','university')):
            return content,[]
        supplied=value.get('visual_inputs',[])
        if not isinstance(supplied,list) or len(supplied)>MAX_IMAGES:
            raise ValueError('Invalid visual result envelope.')
        images=[]
        for entry in supplied:
            if not isinstance(entry,dict): raise ValueError('Invalid image record.')
            image=inspect_image(entry.get('mime_type'),entry.get('data'))
            if any(entry.get(k)!=image[k] for k in ('sha256','width','height','bytes')):
                raise ValueError('Image record identity changed.')
            images.append(image)
        value={k:v for k,v in value.items() if k!='visual_inputs'}
        return json.dumps(value,ensure_ascii=False,separators=(',',':')),images
    return content,[]


def wire_content(message, provider, model):
    text,images=projection(message['role'],message['content'],message.get('tool_name'))
    if not images: return message['content']
    if not supported(provider,model):
        raise ValueError('Image input is not available for this selected model. Use a supported OpenAI model or DeepSeek Flash.')
    if provider in ('openai', 'chatgpt'):
        return [dict(type='input_text',text=text)]+[
            dict(type='input_image',image_url='data:'+i['mime_type']+';base64,'+i['data'],detail='high')
            for i in images]
    return [dict(type='text',text=text)]+[
        dict(type='image_url',image_url=dict(url='data:'+i['mime_type']+';base64,'+i['data'],detail='original'))
        for i in images]


def result_projection(result, *, enabled, image_index=None):
    """Project standard SDK image blocks, retaining exact text and other fields."""
    blocks=result.get('content') if isinstance(result,dict) else None
    if not isinstance(blocks,list) or not any(isinstance(b,dict) and b.get('type')=='image' for b in blocks):
        return None
    output=[]; images=[]; index=0
    for block in blocks:
        if not isinstance(block,dict) or block.get('type')!='image':
            output.append(block); continue
        current=index; index+=1
        meta={k:v for k,v in block.items() if k!='data'}
        meta['image_index']=current
        try:
            image=inspect_image(block.get('mimeType'),block.get('data'))
            meta.update({k:v for k,v in image.items() if k not in ('data','mime_type')})
            selected=(image_index is None or current==image_index) and len(images)<MAX_IMAGES
            meta['visual_status']='included' if enabled and selected else 'not_included'
            if enabled and selected: images.append(image)
            else: meta['reason']='image_not_selected' if enabled else 'model_image_input_unavailable'
        except ValueError as error:
            meta.update(visual_status='not_included',reason='invalid_image',detail=str(error))
        output.append(meta)
    if image_index is not None and image_index>=index:
        raise ValueError('The requested image index does not exist.')
    return {**result,'content':output},images,index
