"""Installed immutable skills own their files, independently of authoring notes."""
import json
from datetime import datetime,timezone

from ...core.records import Failure
from ...core.skills import MAX_SKILLS,MAX_FILE,encode,sha,name as skill_name,revision as skill_revision,path as skill_path

SKILL_SCHEMA='''
CREATE TABLE skill_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1),revision INTEGER NOT NULL);
INSERT INTO skill_state VALUES(1,0);
CREATE TABLE skill_packages(name TEXT PRIMARY KEY,head_revision TEXT NOT NULL,enabled INTEGER NOT NULL CHECK(enabled IN(0,1)),installed_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE skill_revisions(name TEXT NOT NULL REFERENCES skill_packages(name),revision TEXT NOT NULL,manifest TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(name,revision));
CREATE TABLE skill_files(name TEXT NOT NULL,revision TEXT NOT NULL,path TEXT NOT NULL,sha256 TEXT NOT NULL,bytes INTEGER NOT NULL,body BLOB NOT NULL,PRIMARY KEY(name,revision,path),FOREIGN KEY(name,revision) REFERENCES skill_revisions(name,revision));
CREATE TABLE turn_skill_selections(turn_id TEXT PRIMARY KEY REFERENCES turns(id),name TEXT NOT NULL,revision TEXT NOT NULL,FOREIGN KEY(name,revision) REFERENCES skill_revisions(name,revision));
'''


class SkillStore:
    def __init__(self,store):self.store,self.db=store,store.db

    def epoch(self):return self.db.execute('SELECT revision FROM skill_state WHERE singleton=1').fetchone()[0]

    def manifest(self,name,revision=None,*,enabled=False):
        try:
            skill_name(name)
            if revision is not None:skill_revision(revision)
        except ValueError as error:raise Failure('skill_identity',str(error)) from None
        row=self.db.execute('SELECT * FROM skill_packages WHERE name=?',(name,)).fetchone()
        if row is None or enabled and not row['enabled']:raise Failure('skill_unavailable','This skill is missing or disabled.')
        selected=revision or row['head_revision']
        if enabled and selected!=row['head_revision']:raise Failure('skill_changed','This skill has a new revision. Discover or select it again.')
        value=self.db.execute('SELECT manifest FROM skill_revisions WHERE name=? AND revision=?',(name,selected)).fetchone()
        try:
            if value is None:raise ValueError()
            body=value[0];manifest=json.loads(body)
            if sha(body.encode())!=selected or manifest['name']!=name:raise ValueError()
        except (ValueError,KeyError,TypeError):raise Failure('skill_integrity','The installed skill manifest failed its integrity check.') from None
        return dict(manifest,revision=selected,enabled=bool(row['enabled']),current=selected==row['head_revision'])

    def file(self,name,revision,path='SKILL.md',*,enabled=False):
        try:skill_path(path)
        except ValueError as error:raise Failure('skill_path',str(error)) from None
        manifest=self.manifest(name,revision,enabled=enabled);expected=manifest['files'].get(path)
        if expected is None:raise Failure('skill_file','This file is not part of the skill revision.')
        row=self.db.execute('SELECT sha256,bytes,body FROM skill_files WHERE name=? AND revision=? AND path=? AND bytes<=? AND length(body)<=?',(name,revision,path,MAX_FILE,MAX_FILE)).fetchone()
        if (row is None or type(row['body']) is not bytes or len(row['body'])!=expected['bytes']
                or row['bytes']!=expected['bytes'] or row['sha256']!=expected['sha256'] or sha(row['body'])!=expected['sha256']):
            raise Failure('skill_integrity','An installed skill file failed its exact-byte integrity check.')
        return manifest,row['body']

    def status(self,*,enabled=False,offset=0,limit=100):
        items=[]
        for row in self.db.execute('SELECT name FROM skill_packages WHERE enabled=1 OR ?=0 ORDER BY name',(int(enabled),)):
            try:
                item=self.manifest(row['name']);meta=item['metadata']
                items.append(dict(name=item['name'],revision=item['revision'],enabled=item['enabled'],description=meta['description'],
                    compatibility=meta.get('compatibility',''),files=len(item['files']),error=None))
            except Failure as error:
                if not enabled:items.append(dict(name=row['name'],revision='',enabled=False,description='',compatibility='',files=0,error=error.detail))
        return dict(revision=self.epoch(),items=items[offset:offset+limit],total=len(items),next_offset=offset+limit if offset+limit<len(items) else None)

    def install(self,package,expected,*,update=False):
        manifest,revision,files=package['manifest'],package['revision'],package['files'];name=manifest['name'];stamp=datetime.now(timezone.utc).isoformat()
        # The importer validates syntax. This persistence boundary independently
        # verifies that the supplied immutable identity covers every exact file.
        if (sha(encode(manifest).encode())!=revision or set(files)!=set(manifest['files']) or any(
                sha(body)!=manifest['files'][path]['sha256'] or len(body)!=manifest['files'][path]['bytes'] for path,body in files.items())):
            raise Failure('skill_integrity','Prepared skill identity does not match its files.')
        with self.db:
            if self.epoch()!=expected:raise Failure('skill_conflict','Skills changed. Refresh before saving.')
            old=self.db.execute('SELECT * FROM skill_packages WHERE name=?',(name,)).fetchone()
            if old and old['head_revision']==revision:
                for path in files:self.file(name,revision,path)
                return self.status(limit=20)  # Identical reimport never re-enables a disabled revision.
            if old and not update:raise Failure('skill_exists','This name already exists. Review it and choose an explicit update.')
            if not old and self.db.execute('SELECT count(*) FROM skill_packages').fetchone()[0]>=MAX_SKILLS:
                raise Failure('skill_limit','The library supports at most 100 skill names.')
            if not old:self.db.execute('INSERT INTO skill_packages VALUES(?,?,0,?,?)',(name,revision,stamp,stamp))
            found=self.db.execute('SELECT 1 FROM skill_revisions WHERE name=? AND revision=?',(name,revision)).fetchone()
            if not found:
                self.db.execute('INSERT INTO skill_revisions VALUES(?,?,?,?)',(name,revision,encode(manifest),stamp))
                self.db.executemany('INSERT INTO skill_files VALUES(?,?,?,?,?,?)',[(name,revision,path,sha(body),len(body),body) for path,body in files.items()])
            else:
                for path in files:self.file(name,revision,path)
            # Updates need a new enable decision; new instructions cannot inherit
            # a prior revision's opt-in. Earlier revisions remain inspectable.
            self.db.execute('UPDATE skill_packages SET head_revision=?,enabled=0,updated_at=? WHERE name=?',(revision,stamp,name))
            self.db.execute('UPDATE skill_state SET revision=revision+1 WHERE singleton=1')
        return self.status(limit=20)

    def enable(self,name,revision,enabled,expected):
        with self.db:
            if self.epoch()!=expected:raise Failure('skill_conflict','Skills changed. Refresh before saving.')
            item=self.manifest(name,revision)
            if not item['current']:raise Failure('skill_changed','Select the current installed revision.')
            if enabled:
                for path in item['files']:self.file(name,revision,path)
            self.db.execute('UPDATE skill_packages SET enabled=? WHERE name=?',(int(enabled),name))
            self.db.execute('UPDATE skill_state SET revision=revision+1 WHERE singleton=1')
        return self.status(limit=20)

    def select(self,turn_id,selected):
        if selected:
            self.file(selected['name'],selected['revision'],enabled=True)
            self.db.execute('INSERT INTO turn_skill_selections VALUES(?,?,?)',(turn_id,selected['name'],selected['revision']))

    def selected(self,turn_id):
        row=self.db.execute('SELECT name,revision FROM turn_skill_selections WHERE turn_id=?',(turn_id,)).fetchone()
        return dict(row) if row else None
