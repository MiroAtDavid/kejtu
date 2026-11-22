"""Exact course-file projections under the current operator-owned Files grants."""
import asyncio
import base64
from contextlib import contextmanager
import hashlib
import io
import math
import zipfile
import json
import os
import sys
import re
from pathlib import Path
import shutil
import stat
import tempfile

from ...core.records import Failure
from ...core.reference import exact_page
from ...core.vision import inspect_image
from .files import grants
from .code_files import CODE_SUFFIXES, MAX_TEXT_BYTES, CodeSaves, validate_text

MAX_FILE_BYTES=32*1024*1024
TEXT_SUFFIXES=CODE_SUFFIXES | {'.md','.markdown','.csv','.tsv','.tex','.rst'}


MAX_LAYOUT_BYTES=8*1024*1024
_XML_CONTROL=re.compile(rb'[\x00-\x08\x0b\x0c\x0e-\x1f]')

def pdf_layout(content,rotation=0):
    """Paragraph boxes of one page from Poppler's -bbox-layout XHTML.

    Boxes are PDF points from the top-left corner of the page as displayed.
    Poppler already places words after the page's /Rotate but reports the
    unrotated page size, so a quarter turn swaps width and height. Each block's
    text is its words in Poppler's reading order, joined by spaces on one line.
    """
    if rotation not in (0,90,180,270):
        raise Failure('material_projection','Paragraph positions could not be read for this page.')
    from xml.etree import ElementTree
    def unreadable():
        return Failure('material_projection','Paragraph positions could not be read for this page.')
    def local(node):
        return node.tag.rsplit('}',1)[-1]
    def number(node,key):
        try:value=float(node.get(key))
        except (TypeError,ValueError):raise unreadable() from None
        if not math.isfinite(value):raise unreadable()
        return value
    try:root=ElementTree.fromstring(_XML_CONTROL.sub(b'',content))
    except ElementTree.ParseError:raise unreadable() from None
    pages=[node for node in root.iter() if local(node)=='page']
    if len(pages)!=1:raise unreadable()
    width,height=number(pages[0],'width'),number(pages[0],'height')
    if width<=0 or height<=0:raise unreadable()
    blocks=[]
    for block in pages[0].iter():
        if local(block)!='block':continue
        text=' '.join(' '.join(word.text or '' for word in block.iter() if local(word)=='word').split())
        if not text:continue
        x0,y0,x1,y1=(number(block,key) for key in ('xMin','yMin','xMax','yMax'))
        if x1<x0 or y1<y0:raise unreadable()
        blocks.append(dict(box=[round(x0,2),round(y0,2),round(x1,2),round(y1,2)],text=text))
    if rotation in (90,270):width,height=height,width
    return dict(page_width=round(width,2),page_height=round(height,2),blocks=blocks)

def image_record(mime,raw):
    try:return inspect_image(mime,base64.b64encode(raw).decode())
    except ValueError as error:raise Failure('material_image',str(error)) from None


class CourseMaterials:
    def __init__(self,store,files):
        self.store,self.files=store,files
        self.code_saves = CodeSaves()

    def code_writable(self, target):
        _, config = self._settings()
        matching = (g for g in grants(config) if Path(g['path']) in target.parents)
        grant = max(matching, key=lambda g:len(Path(g['path']).parts), default=None)
        if not grant or grant['access'] != 'write':
            return False
        try:
            self.files.validate_connection_paths([], [str(target)])
            info = target.stat(follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_mode & (stat.S_ISUID | stat.S_ISGID)):
                return False
            if os.name != 'nt' and (info.st_uid != os.geteuid()
                    or (hasattr(os, 'listxattr') and 'security.capability' in os.listxattr(target, follow_symlinks=False))):
                return False
            return os.access(target, os.W_OK) and os.access(target.parent, os.W_OK)
        except (Failure, OSError):
            return False

    def _settings(self):
        if self.files is None:
            raise Failure('files_unavailable','Configure shared folders in Settings → Files.')
        value=self.files.host.connections.read()
        config=value.get('files')
        if not config or not config['enabled']:
            raise Failure('files_disabled','Shared folder access is disabled. Course assignment does not grant access.')
        return value['revision'],config

    def path(self,course_id,relative=''):
        revision,config=self._settings()
        course=self.store.planner._live('course',course_id)['data']
        if not course['folder']:
            raise Failure('course_folder','Choose a shared material folder for this course.')
        if sys.platform == 'win32':
            from .windows_runtime import folder_input
            folder_input(course['folder'])
        root=Path(course['folder'])
        if not root.is_absolute() or root.resolve()!=root:
            raise Failure('course_folder','The saved course folder moved or changed its identity; choose it again.')
        suffix=Path(relative)
        if suffix.is_absolute() or suffix.drive or '..' in suffix.parts or '\x00' in str(suffix):
            raise Failure('material_path','Choose a relative file inside the course folder.')
        target=root/suffix
        if sys.platform == 'win32':
            from .windows_runtime import folder_input
            folder_input(str(target))
        if self.files.is_private(target):
            raise Failure('files_private','The private archive is not course material.')
        if not any(target==Path(g['path']) or Path(g['path']) in target.parents for g in grants(config)):
            raise Failure('files_forbidden','The course folder is no longer shared. Update Settings → Files.')
        # Descendant symlinks do not confer another path's access or identity.
        if target.resolve()!=target:
            raise Failure('material_path','Choose an original course path rather than a symlink.')
        return revision,root,target

    def validate_folder(self,folder):
        _,config=self._settings()
        path=Path(folder)
        if sys.platform == 'win32':
            from .windows_runtime import folder_input
            folder_input(str(path))
        if not path.is_absolute() or not path.is_dir() or path.resolve()!=path:
            raise Failure('course_folder','Choose an existing canonical shared folder.')
        if self.files.is_private(path):
            raise Failure('files_private','The private archive is not a course folder.')
        if not any(path==Path(g['path']) or Path(g['path']) in path.parents for g in grants(config)):
            raise Failure('files_forbidden','Choose a folder already shared through Settings → Files.')
        return str(path)

    def source_path(self,path):
        """An already linked original keeps its path when a course folder changes.

        Exact saved identity plus today's Files grants decide access; the current
        course folder must never silently substitute another original.
        """
        revision,config=self._settings()
        target=Path(path)
        if sys.platform == 'win32':
            from .windows_runtime import folder_input
            folder_input(str(target))
        if not target.is_absolute() or target.resolve()!=target:
            raise Failure('material_path','This saved original path changed its identity.')
        if self.files.is_private(target):
            raise Failure('files_private','The private archive is not course material.')
        if not any(target==Path(g['path']) or Path(g['path']) in target.parents for g in grants(config)):
            raise Failure('files_forbidden','The original source is no longer shared. Update Settings → Files.')
        return revision,target.parent,target

    def snapshot(self,course_id,artifact_id,*,offset=0,maximum=16384):
        """Page a retained exact projection under today's Files permission.

        A changed/missing original is reported separately from snapshot identity.
        Keeping an artifact never perpetuates a revoked shared-folder grant.
        """
        if type(offset) is not int or not 0<=offset<2**63 or type(maximum) is not int or not 1<=maximum<=32768:
            raise Failure('material_page','Choose a bounded exact UTF-8 source page.')
        row=self.store.db.execute('SELECT * FROM planner_material WHERE course_id=? AND artifact_id=?',
                                  (course_id,artifact_id)).fetchone()
        if row is None:raise Failure('material_missing','This course source projection is unavailable.')
        self.store.planner._live('course',course_id)
        _,root,target=self.source_path(row['path'])
        try:
            raw,_=self._read(root,target)
            current='unchanged' if hashlib.sha256(raw).hexdigest()==row['source_sha256'] else 'changed'
        except Failure as error:
            if error.code!='material_unavailable':raise
            current='unavailable'
        body=self.store._body(artifact_id)
        source=dict(path=row['path'],sha256=row['source_sha256'],page=row['page'],artifact_id=artifact_id)
        common=dict(source=source,representation=row['representation'],current_file_status=current)
        if row['representation'] in ('image','pdf_image'):
            return dict(common,image=json.loads(body),complete=True)
        page=exact_page(artifact_id,body,offset,maximum).envelope()
        if page.get('reason'):raise Failure('material_page',page['reason'])
        return dict(common,**page,complete=page['next_offset'] is None)

    @contextmanager
    def _open(self,root,target,*,directory=False):
        """Open originals without following links; directory policy is platform-specific."""
        if sys.platform == 'win32':
            from ..storage.files import open_regular
            try:
                if directory:
                    from ..storage.files import held_windows_directory
                    with held_windows_directory(target):
                        yield target, target.stat(follow_symlinks=False)
                else:
                    with open_regular(target) as source:
                        info=os.fstat(source.fileno())
                        if info.st_nlink != 1: raise OSError('Multiply linked file is not shared.')
                        yield source.fileno(),info
            except OSError:
                raise Failure('material_unavailable','The selected original is unavailable or changed; refresh the material list.') from None
            return
        descriptors=[]
        try:
            fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
            descriptors.append(fd)
            parts=target.parts[1:]
            for index,part in enumerate(parts):
                flags=os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK
                if index<len(parts)-1 or directory:flags|=os.O_DIRECTORY
                fd=os.open(part,flags,dir_fd=fd)
                descriptors.append(fd)
            info=os.fstat(fd)
            if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
                raise Failure('material_type','Choose an ordinary course file or directory.')
            yield fd,info
        except OSError:
            raise Failure('material_unavailable','The selected original is unavailable or changed; refresh the material list.') from None
        finally:
            for descriptor in reversed(descriptors):os.close(descriptor)

    def listing(self,course_id,relative='',*,offset=0,limit=50):
        if type(offset) is not int or not 0<=offset<=100000 or type(limit) is not int or not 1<=limit<=100:
            raise Failure('material_page','Choose a bounded directory page.')
        revision,root,target=self.path(course_id,relative)
        with self._open(root,target,directory=True) as (fd,_):
            entries=[]
            with os.scandir(fd) as scanner:
                for index,item in enumerate(scanner):
                    if index>=10000:
                        raise Failure('material_capacity','This directory has more than 10,000 entries; choose a narrower folder.')
                    if item.is_symlink():continue
                    if sys.platform == 'win32' and item.stat(follow_symlinks=False).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:continue
                    if self.files.is_private(target/item.name):continue
                    try:kind='folder' if item.is_dir(follow_symlinks=False) else 'file' if item.is_file(follow_symlinks=False) else None
                    except OSError:continue
                    if kind:
                        entry=dict(name=item.name,path=str((target/item.name).relative_to(root)),kind=kind,
                                   bytes=item.stat(follow_symlinks=False).st_size if kind=='file' else 0)
                        if kind == 'file' and Path(item.name).suffix.lower() in CODE_SUFFIXES:
                            entry['editor'] = 'code'
                        entries.append(entry)
        entries.sort(key=lambda r:(r['kind']!='folder',r['name'].casefold(),r['name']))
        if self._settings()[0]!=revision:self.path(course_id,relative)
        return dict(course_id=course_id,path=relative,items=entries[offset:offset+limit],
                    next_offset=offset+limit if len(entries)>offset+limit else None,total=len(entries))

    def _read(self,root,target):
        with self._open(root,target) as (fd,before):
            if before.st_size>MAX_FILE_BYTES:
                raise Failure('material_size','Choose a course file within the 32 MB local-reader limit.')
            chunks=[];size=0
            while True:
                chunk=os.read(fd,min(65536,MAX_FILE_BYTES+1-size))
                if not chunk:break
                chunks.append(chunk);size+=len(chunk)
                if size>MAX_FILE_BYTES:
                    raise Failure('material_size','The file grew beyond the local-reader size limit.')
            after=os.fstat(fd)
            if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):
                raise Failure('material_changed','The file changed during reading; refresh before continuing.')
        return b''.join(chunks),before

    def document(self,course_id,relative,*,offset=0,sha256=None):
        """Local UI bytes only, paged under the existing live course/Files authority.

        Each chunk re-reads and hashes one stable original. A changed file can
        never be spliced into the opened document; no bytes enter model context.
        """
        if type(offset) is not int or not 0<=offset<MAX_FILE_BYTES:
            raise Failure('material_page','Choose a valid document byte offset.')
        if sha256 is not None and (not isinstance(sha256,str) or not re.fullmatch('[0-9a-f]{64}',sha256)):
            raise Failure('material_identity','Use the opened document identity.')
        if offset and sha256 is None:
            raise Failure('material_identity','Continue with the same opened document identity.')
        revision,root,target=self.path(course_id,relative)
        code = target.suffix.lower() in CODE_SUFFIXES
        if not code and target.suffix.lower() not in ('.pdf','.docx','.md','.markdown'):
            raise Failure('material_format','Choose a supported document or code file.')
        raw,info=self._read(root,target)
        if code:
            if info.st_nlink != 1:
                raise Failure('material_path', 'Open an independent copy of this linked file.')
            validate_text(raw)
        if target.suffix.lower()=='.pdf' and not raw.startswith(b'%PDF-'):
            raise Failure('material_format','The selected file is not a PDF.')
        if target.suffix.lower()=='.docx':
            try:
                # Inspect the directory only: never extract or decompress untrusted members here.
                with zipfile.ZipFile(io.BytesIO(raw)) as package:
                    if not {'[Content_Types].xml','word/document.xml'}<=set(package.namelist()):
                        raise ValueError('not a Word document')
                    if len(package.infolist())>10000 or sum(i.file_size for i in package.infolist())>128*1024*1024:
                        raise ValueError('expanded document exceeds the reader limit')
            except (ValueError,zipfile.BadZipFile):
                raise Failure('material_format','Choose an unencrypted DOCX document within the reader limits.') from None
        if target.suffix.lower() in ('.md','.markdown'):
            if len(raw)>MAX_TEXT_BYTES:
                raise Failure('material_size','Choose a Markdown document within the 2 MB editor limit.')
            try:raw.decode('utf-8')
            except UnicodeError:
                raise Failure('material_encoding','Choose a UTF-8 Markdown document.') from None
            if b'\x00' in raw:
                raise Failure('material_encoding','This Markdown file contains binary data.')
        digest=hashlib.sha256(raw).hexdigest()
        if sha256 is not None and digest!=sha256:
            raise Failure('material_changed','This document changed. Reopen it before continuing.')
        if offset>=len(raw) and not (offset==0 and not raw):raise Failure('material_page','This document byte offset is outside the file.')
        if self._settings()[0]!=revision:
            raise Failure('files_changed','File permissions changed during reading.')
        end=min(len(raw),offset+1024*1024)
        result = dict(path=str(target),sha256=digest,total_bytes=len(raw),offset=offset,
                      next_offset=end if end<len(raw) else None,data=base64.b64encode(raw[offset:end]).decode())
        if code:
            result['writable'] = self.code_writable(target)
        return result

    async def _process(self,program,args,source,maximum):
        if sys.platform == 'win32':
            raise Failure('material_unavailable', 'PDF reading is not available in this Windows test build yet. No document helper was started.')
        executable=shutil.which(program,path='/usr/bin:/bin')
        if not executable:
            raise Failure('material_runtime',f'{program} is unavailable; repair the document-reader installation.')
        return await self._run_reader(executable, args, source, maximum)

    @staticmethod
    async def _run_reader(executable, args, source, maximum):
        child=await asyncio.create_subprocess_exec(executable,*args,stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
            env=dict(os.environ,LC_ALL='C'))
        async def feed():
            try:child.stdin.write(source);await child.stdin.drain()
            except (BrokenPipeError,ConnectionResetError):pass
            finally:child.stdin.close()
        writer=asyncio.create_task(feed())
        try:
            async with asyncio.timeout(20):
                chunks=[];size=0
                while True:
                    chunk=await child.stdout.read(min(65536,maximum+1-size))
                    if not chunk:break
                    chunks.append(chunk);size+=len(chunk)
                    if size>maximum:
                        raise Failure('material_size','This document projection is too large; choose a smaller page or view.')
                await child.wait()
                if child.returncode:
                    raise Failure('material_projection','This PDF page could not be read. Check its page number or protection.')
                return b''.join(chunks)
        except TimeoutError:
            raise Failure('material_timeout','The local document reader timed out; no source projection was saved.') from None
        finally:
            if child.returncode is None:child.kill()
            await child.wait()
            writer.cancel();await asyncio.gather(writer,return_exceptions=True)

    async def _windows_pdf(self, raw, page, view):
        # Poppler is a Linux dependency. The Windows package already includes
        # this fixed CLI. It only receives the authorized bytes in a temporary
        # file; the original path is never passed back to an external program.
        if view == 'layout':
            raise Failure('material_unavailable', 'Paragraph positions are not available in the Windows build yet.')
        from .windows_connections import PROGRAMS, installed
        try:
            executable = installed(self.files.host.root, 'package-runtime/craft/' + PROGRAMS['pdfcraft'])
        except ValueError as error:
            raise Failure('material_runtime', str(error)) from None
        with tempfile.TemporaryDirectory(prefix='kejtu-pdf-') as temporary:
            original = Path(temporary) / 'document.pdf'
            original.write_bytes(raw)
            info = await self._run_reader(executable, ['info', original], b'', MAX_TEXT_BYTES)
            try:
                pages = json.loads(info)['pages']
                if type(pages) is not int or not 1 <= pages <= 100000:
                    raise ValueError
            except (TypeError, KeyError, ValueError):
                raise Failure('material_projection', 'The PDF page count could not be determined.') from None
            if page > pages:
                raise Failure('material_projection', 'This page is outside the PDF.')
            if view == 'text':
                text = await self._run_reader(executable, ['text', original, '--page', str(page)], b'', MAX_TEXT_BYTES)
                return pages, text
            rendered = Path(temporary) / 'page.jpg'
            await self._run_reader(executable, ['render', original, '--page', str(page), '--dpi', '96',
                                               '--out', rendered], b'', 65536)
            with rendered.open('rb') as image:
                data = image.read(1400001)
            if len(data) > 1400000:
                raise Failure('material_size', 'This PDF page is too large to display. Choose exact text.')
            return pages, data

    async def read(self,course_id,relative,*,page=1,offset=0,maximum=16384,view='text'):
        if type(page) is not int or not 1<=page<=100000 or view not in ('text','image','layout'):
            raise Failure('material_page','Choose a page number and text, image or layout view.')
        if type(offset) is not int or offset<0 or type(maximum) is not int or not 1<=maximum<=32768:
            raise Failure('material_page','Choose a valid byte offset and page size of at most 32 KB.')
        revision,root,target=self.path(course_id,relative)
        raw,original_info=self._read(root,target)
        digest=hashlib.sha256(raw).hexdigest()
        suffix=target.suffix.lower()
        metadata={}
        if suffix=='.pdf':
            if not raw.startswith(b'%PDF-'):
                raise Failure('material_projection','The selected file is not a PDF.')
            # Count and projection use the same already-authorized bytes. The reader
            # can stop at the final page without probing invalid pages or guessing.
            if sys.platform == 'win32':
                pages, content = await self._windows_pdf(raw, page, view)
            else:
                info=await self._process('pdfinfo',['-'],raw,65536)
                count=re.search(rb'^Pages:\s+(\d+)\s*$',info,re.MULTILINE)
                if count is None or not 1<=int(count[1])<=100000:
                    raise Failure('material_projection','The PDF page count could not be determined.')
                pages=int(count[1])
                if page>pages:
                    raise Failure('material_projection','This PDF page could not be read. Check its page number or protection.')
                if view=='text':
                    content=await self._process('pdftotext',['-f',str(page),'-l',str(page),'-layout','-enc','UTF-8','-','-'],raw,MAX_TEXT_BYTES)
                elif view=='layout':
                    # Same Poppler page box as pdftoppm (media box). The page
                    # size Poppler reports for the boxes ignores /Rotate.
                    detail=await self._process('pdfinfo',['-f',str(page),'-l',str(page),'-'],raw,65536)
                    turn=re.search(rb'^Page\s+%d rot:\s+(\d+)\s*$' % page,detail,re.MULTILINE)
                    if turn is None or int(turn[1])%90:
                        raise Failure('material_projection','Paragraph positions could not be read for this page.')
                    rotation=int(turn[1])%360
                    content=await self._process('pdftotext',['-f',str(page),'-l',str(page),'-bbox-layout','-enc','UTF-8','-','-'],raw,MAX_LAYOUT_BYTES)
                else:
                    content=await self._process('pdftoppm',['-f',str(page),'-l',str(page),'-singlefile','-scale-to','1600','-jpeg','-'],raw,1400000)
            metadata['page_count'] = pages
            if view == 'text':
                body = content.decode('utf-8')
                representation = 'pdf_text'
            elif view == 'layout':
                body = json.dumps(pdf_layout(content, rotation), ensure_ascii=False, separators=(',', ':'))
                representation = 'pdf_layout'
            else:
                body = json.dumps(image_record('image/jpeg', content), separators=(',', ':'))
                representation = 'pdf_image'
        elif suffix in ('.png','.jpg','.jpeg'):
            if view!='image':
                raise Failure('material_image','This original is an image. Choose image view; no OCR text is implied.')
            body=json.dumps(image_record('image/png' if suffix=='.png' else 'image/jpeg',raw),separators=(',',':'))
            representation='image';page=0
        elif suffix in TEXT_SUFFIXES and view=='text':
            if len(raw)>MAX_TEXT_BYTES:
                raise Failure('material_size','This text file exceeds the local reader limit; use the existing permitted file tools.')
            try:body=raw.decode('utf-8')
            except UnicodeError:raise Failure('material_encoding','This file is not UTF-8 text; use a suitable document tool.') from None
            representation='text';page=0
        else:
            raise Failure('material_format','Use a PDF, PNG/JPEG image or supported UTF-8 text file with this reader. Other formats remain available through permitted document tools.')
        # Any asynchronous projection can overlap an explicit permission change.
        current,_,_=self.path(course_id,relative)
        if current!=revision:
            raise Failure('files_changed','File permissions changed during reading; inspect current access before retrying.')
        with self._open(root,target) as (_,info):
            if (info.st_ino,info.st_size,info.st_mtime_ns)!=(original_info.st_ino,original_info.st_size,original_info.st_mtime_ns):
                raise Failure('material_changed','The original changed during projection; refresh before continuing.')
        artifact=self.store.planner.material(course_id,str(target),digest,page,representation,body)
        source=dict(path=str(target),sha256=digest,page=page,artifact_id=artifact)
        if representation in ('image','pdf_image'):
            return dict(source=source,representation=representation,image=json.loads(body),complete=True,**metadata)
        result=exact_page(artifact,body,offset,maximum).envelope()
        if result.get('reason'):
            raise Failure('material_page',result['reason'])
        return dict(source=source,representation=representation,**result,**metadata,
                    complete=result['next_offset'] is None,
                    notice='No selectable text on this page; use its image view.' if not body.strip() else None)
