"""Minimal development commands. IO is explicit; no implicit state discovery."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import stat
import sys
import time

from .. import __version__
from ..application.turn import TurnService
from ..core.prompt import system_prompt
from ..core.records import Failure, Profile
from ..core.execution import HistoryPolicy


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog='kejtu-next',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='R012 development conversation core (isolated).\nDefault: no tools, retrieval or desktop.\nOptional: attached-reference read or --history (up to 16 operations under one ledger).\n--history --memory adds read-only candidate memory; --notes-write explicitly permits owned-note saves. Legacy transcripts require the explicit offline import-conversations command.',
        epilog='Offline checks: PYTHONPATH=src python -B -m unittest discover -s tests -v')
    parser.add_argument('--version', action='version', version=f'kejtu-next {__version__}')
    commands = parser.add_subparsers(dest='command')
    command = commands.add_parser('import-conversations', help='Offline adoption of an explicit legacy snapshot; no inference.')
    command.add_argument('--state-dir', required=True, type=Path)
    command.add_argument('--expected-state-id', required=True)
    command.add_argument('--snapshot', required=True, type=Path)
    command.add_argument('--source-id', required=True, help='Stable 32-hex archive identity; reuse on retry.')
    command.add_argument('--profile', required=True, type=Path, help='Seed only; existing saved model profiles remain authoritative.')
    command.add_argument('--backup-dir', required=True, type=Path)
    for name in ('upgrade-store', 'restore-upgrade-backup'):
        command = commands.add_parser(name, help='Explicit offline schema operation; never contacts a model.')
        command.add_argument('--state-dir', required=True, type=Path)
        command.add_argument('--expected-state-id', required=True)
        command.add_argument('--backup-dir', required=True, type=Path)
    for name in ('init', 'new', 'send', 'show', 'read-reference', 'preview-note-removal',
                 'remove-note', 'note-removal-status'):
        command = commands.add_parser(name)
        command.add_argument('--state-dir', required=True, type=Path)
        if name == 'new':
            command.add_argument('--profile', required=True, type=Path)
            command.add_argument('--standing-profile', type=Path)
            command.add_argument('--reference-file', type=Path)
            command.add_argument('--history', action='store_true', help='New immutable history grant; standard 16-operation policy.')
            command.add_argument('--memory', action='store_true', help='Requires --history; explicit read-only candidate bank grant, default off.')
            command.add_argument('--notes-write', action='store_true', help='Requires --history --memory; permits deliberate owned-note writes, default off.')
            command.add_argument('--history-policy', choices=('standard', 'r007-development'),
                                 help='Requires --history; default standard. R007 is a two-operation test fixture only.')
        if name in ('send', 'show', 'read-reference', 'preview-note-removal', 'remove-note', 'note-removal-status'):
            command.add_argument('--conversation', required=True)
        if name in ('preview-note-removal', 'remove-note'):
            command.add_argument('--note-id', required=True)
        if name in ('remove-note', 'note-removal-status'):
            command.add_argument('--operation-id', required=True, help='Choose a 32-character lowercase hex ID; reuse it only for the same intent.')
        if name == 'remove-note':
            command.add_argument('--preview-token', required=True, help='Exact token from the preview; all retained revisions will be removed.')
        if name == 'send':
            command.add_argument('--include-turn', action='append', default=[], help='Protect this older whole turn (history mode only).')
            source = command.add_mutually_exclusive_group(required=True)
            source.add_argument('--message')
            source.add_argument('--message-file', type=Path)
        if name == 'show':
            command.add_argument('--after', type=int, default=0)
            command.add_argument('--limit', type=int, default=50)
        if name == 'read-reference':
            command.add_argument('--offset', type=int, default=0)
            command.add_argument('--max-bytes', type=int, default=4096)
    args = parser.parse_args(argv)
    if args.command == 'new' and args.history_policy is not None and not args.history:
        parser.error('--history-policy requires --history')
    if args.command == 'new' and args.memory and not args.history:
        parser.error('--memory requires --history')
    if args.command == 'new' and args.notes_write and not args.memory:
        parser.error('--notes-write requires --history --memory')
    if args.command is None:
        parser.print_help()
    return args


def read_text(path: Path, maximum: int) -> str:
    with path.open('rb') as source:
        raw = source.read(maximum + 1)
    if len(raw) > maximum:
        raise Failure('input_size', 'Selected file exceeds the development input bound; nothing was saved.')
    return raw.decode('utf-8')


async def execute(args, service: TurnService) -> int:
    store = service.store
    if args.command == 'init':
        print(json.dumps({'status': 'initialized', 'application': 'kejtu-next-r011'}))
    elif args.command == 'new':
        policy = (HistoryPolicy.R007 if args.history_policy == 'r007-development'
                  else HistoryPolicy.STANDARD) if args.history else None
        profile = Profile(**json.loads(read_text(args.profile, 16384)))
        standing = read_text(args.standing_profile, profile.context_limit) if args.standing_profile else ''
        reference = None
        if args.reference_file is not None:
            fd = os.open(args.reference_file, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise Failure('reference_file', 'A regular UTF-8 file must be explicitly selected.')
                raw = source.read(4 * profile.context_limit + 1)
            if len(raw) > 4 * profile.context_limit:
                raise Failure('input_size', 'Selected reference exceeds the snapshot bound; nothing was saved.')
            reference = raw.decode('utf-8')
        ident = await service.create_conversation(profile, system_prompt(standing, with_reference=reference is not None,
                                          with_history=args.history, history_policy=policy, with_memory=args.memory,
                                          with_notes=args.notes_write),
                                          reference_body=reference, history_enabled=args.history, history_policy=policy,
                                          memory_enabled=args.memory, notes_enabled=args.notes_write,
                                          hybrid_memory=args.memory)
        print(json.dumps({'conversation_id': ident}))
    elif args.command == 'send':
        profile = store.conversation(args.conversation).profile
        user = (read_text(args.message_file, profile.context_limit * 4)
                if args.message_file else args.message)
        result = await service.send(args.conversation, user, include_turns=args.include_turn)
        if result.status == 'completed':
            sys.stdout.write(result.answer)
            return 0
        print(json.dumps(asdict(result), ensure_ascii=False), file=sys.stderr)
        return 1
    elif args.command == 'show':
        turns = store.turns(args.conversation, after=args.after, limit=args.limit)
        reads = [r for t in turns for r in store.reads(t.id)]
        notes = []
        for turn in turns:
            if store.conversation(turn.conversation_id).notes_enabled:
                outcome = store.note_outcomes(turn.id)
                notes.append({'turn_id': turn.id, **outcome})
                if outcome['status'] == 'unavailable':
                    # Stored delivery metadata is not a bypass around revoked
                    # bank/revision authority at explicit outcome inspection.
                    for row in reads:
                        if row['turn_id'] == turn.id and row['name'] in ('write_note', 'note_outcome'):
                            row.update(arguments='{}', result=json.dumps(outcome))
        value = {'turns': [asdict(t) for t in turns],
            'reads': reads,
            'admissions': [record for t in turns if (record := store.admission(t.id)) is not None],
            'next_after': turns[-1].sequence if turns else args.after}
        if notes:
            value['note_outcomes'] = notes
        print(json.dumps(value, ensure_ascii=False))
    elif args.command == 'read-reference':
        profile = store.conversation(args.conversation).profile
        if not 1 <= args.max_bytes <= profile.context_limit:
            raise Failure('input_size', 'Explicit reread pages must fit within the saved context byte bound.')
        page = store.read_reference(args.conversation, args.offset, args.max_bytes,
                                    time.monotonic() + profile.timeout_seconds)
        print(page.encoded())
        return 1 if page.reason else 0
    elif args.command == 'preview-note-removal':
        print(json.dumps(store.preview_note_removal(args.conversation, args.note_id), ensure_ascii=False))
    elif args.command == 'remove-note':
        print(json.dumps(store.remove_note(args.conversation, args.note_id, args.preview_token, args.operation_id)))
    elif args.command == 'note-removal-status':
        print(json.dumps(store.note_removal_status(args.conversation, args.operation_id)))
    return 0
