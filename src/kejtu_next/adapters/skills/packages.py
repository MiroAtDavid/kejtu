"""Standard SKILL.md parsing and explicit bounded local import.

The YAML parser is loaded only on an operator import. This module never executes
package scripts, follows symlinks or scans a developer's skill directories.
"""
import json
import os
from pathlib import Path
import re
import stat
import time

from ...core.skills import MAX_FILES, MAX_FILE, MAX_PACKAGE, MAX_INSTRUCTIONS, name, path, sha, encode


def metadata(data, dirname=None):
    import yaml
    class Frontmatter(yaml.SafeLoader):
        def compose_node(self,parent,index):
            if self.check_event(yaml.AliasEvent):raise ValueError('YAML aliases are not supported in skill metadata.')
            return super().compose_node(parent,index)
        def construct_mapping(self,node,deep=False):
            result={}
            for key,value in self.construct_pairs(node,deep=deep):
                if not isinstance(key,str) or key in result:raise ValueError('Metadata requires unique string keys.')
                result[key]=value
            return result
    if len(data)>MAX_INSTRUCTIONS:raise ValueError('SKILL.md exceeds the 64 KiB complete-instruction limit.')
    text=data.decode('utf-8');match=re.match(r'\A\ufeff?---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)',text,re.S)
    if not match or len(match[1])>12000:raise ValueError('SKILL.md needs YAML name and description between --- lines.')
    try:meta=yaml.load(match[1],Loader=Frontmatter)
    except (yaml.YAMLError,RecursionError):raise ValueError('Invalid skill YAML metadata.') from None
    if not isinstance(meta,dict):raise ValueError('Skill metadata must be a mapping.')
    # The declared name owns installed identity. A download/repository folder
    # may have another name; retain its origin and every file without rewriting.
    # Keep dirname accepted for existing importer callers, not as an authority.
    name(meta.get('name'))
    if not isinstance(meta.get('description'),str) or not meta['description'].strip() or len(meta['description'])>1024:
        raise ValueError('Give the skill a description of 1–1024 characters.')
    for field in ('license','compatibility','allowed-tools'):
        if field in meta and not isinstance(meta[field],str):raise ValueError(field+' must be text.')
    if 'compatibility' in meta and not 1<=len(meta['compatibility'])<=500:raise ValueError('Compatibility must be 1–500 characters.')
    if 'metadata' in meta and (not isinstance(meta['metadata'],dict) or any(
            not isinstance(k,str) or not isinstance(v,str) for k,v in meta['metadata'].items())):
        raise ValueError('The metadata field must map text keys to text values.')
    try:encode(meta)
    except (ValueError,TypeError,RecursionError):raise ValueError('Metadata values must be ordinary JSON-compatible data.') from None
    if not text[match.end():].strip():raise ValueError('Add procedure instructions after the YAML metadata.')
    return meta


def prepare(files,origin,dirname=None):
    if not isinstance(files,dict) or 'SKILL.md' not in files or not 1<=len(files)<=MAX_FILES:
        raise ValueError('Import one skill with SKILL.md and at most 128 files.')
    if any(path(p)!=p or not isinstance(b,bytes) or len(b)>MAX_FILE for p,b in files.items()):
        raise ValueError('Each skill file must be at most 2 MiB.')
    if sum(map(len,files.values()))>MAX_PACKAGE:raise ValueError('A skill package must be at most 8 MiB.')
    meta=metadata(files['SKILL.md'],dirname)
    manifest=dict(name=meta['name'],metadata=meta,origin=origin,
        files={p:dict(sha256=sha(b),bytes=len(b)) for p,b in sorted(files.items())})
    return dict(manifest=manifest,revision=sha(encode(manifest).encode()),files=files)


def local(source,check=lambda:None):
    folder=Path(source)
    if not folder.is_absolute() or folder.resolve()!=folder or not folder.is_dir() or folder.is_symlink():
        raise ValueError('Choose a real absolute skill folder without symbolic links.')
    files={};total=0;directories=0
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    # Resolve every component through held directory descriptors. Renaming a
    # parent or swapping a child for a symlink cannot redirect an import.
    root=os.open('/',flags)
    try:
        for component in folder.parts[1:]:
            child=os.open(component,flags,dir_fd=root)
            os.close(root);root=child
        def visit(directory,prefix='',depth=0):
            nonlocal total,directories
            check();directories+=1
            if directories>256 or depth>16:raise ValueError('Skill folder has too many nested directories.')
            for item in sorted(os.listdir(directory)):
                check()
                info=os.stat(item,dir_fd=directory,follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):raise ValueError('Skill folders cannot contain symbolic links.')
                if item=='.git' and stat.S_ISDIR(info.st_mode):continue
                relative=path(prefix+item)
                if stat.S_ISDIR(info.st_mode):
                    child=os.open(item,flags,dir_fd=directory)
                    try:visit(child,relative+'/',depth+1)
                    finally:os.close(child)
                    continue
                fd=os.open(item,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
                with os.fdopen(fd,'rb') as f:
                    opened=os.fstat(f.fileno())
                    if not stat.S_ISREG(opened.st_mode) or opened.st_size>MAX_FILE:raise ValueError('Import bounded regular files only.')
                    body=f.read(MAX_FILE+1)
                total+=len(body);files[relative]=body
                if len(body)>MAX_FILE or len(files)>MAX_FILES or total>MAX_PACKAGE:raise ValueError('Skill package exceeds its file/byte limit.')
        visit(root)
    finally:os.close(root)
    return prepare(files,dict(kind='local',directory=str(folder)),folder.name)


def load_source(kind,source,check,github=None):
    if kind=='text':return prepare({'SKILL.md':source.encode()},dict(kind='pasted'))
    if kind=='folder':return local(source,check)
    if kind=='github' and github:
        files,dirname,origin=github(source,check=check,deadline=time.monotonic()+30)
        return prepare(files,origin,dirname)
    raise ValueError('Choose a skill folder, SKILL.md text, or a supported public GitHub folder.')


class PackageImporter:
    """A disposable reader makes Stop/deadlines effective even on a stalled disk."""
    async def load(self,kind,source,check):
        import asyncio
        import base64
        import signal
        import sys
        process=None;read=None
        try:
            check()
            env={'PATH':'/usr/bin:/bin','HOME':'/tmp','LANG':'C.UTF-8',
                'PYTHONPATH':str(Path(__file__).resolve().parents[4]/'src'),'PYTHONDONTWRITEBYTECODE':'1'}
            process=await asyncio.create_subprocess_exec(sys.executable,'-B','-m','kejtu_next','--skill-import-worker',
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
                env=env,start_new_session=True)
            read=asyncio.create_task(process.communicate(json.dumps(dict(kind=kind,source=source)).encode()))
            while not read.done():
                await asyncio.wait({read},timeout=.1);check()
            data,_=read.result();check()
            if len(data)>14*1024**2 or process.returncode!=0:raise ValueError('Skill reader did not return a complete bounded package.')
            result=json.loads(data)
            if not result['ok']:raise ValueError(result['error'])
            package=result['package'];package['files']={p:base64.b64decode(b,validate=True) for p,b in package['files'].items()}
            return package
        finally:
            if process:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                await process.wait()
            if read:
                if not read.done():read.cancel()
                await asyncio.gather(read,return_exceptions=True)
