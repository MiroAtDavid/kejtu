"""Bundled Linux terminal connections. Registration alone never enables a tool."""
from pathlib import Path
import re
import subprocess
import sys

from .config import connection
from .files import WORKING_FOLDER
from ...core.records import Failure

DIRECTORY = 'terminals-v1'
NAMES = ('terminal-use', 'quick-terminal')
NODE = '/usr/bin/node'
CLI = Path('node_modules/terminal-use/bin/terminal-use.js')
INTERACTIVE = ('batch', 'click', 'create', 'destroy', 'list', 'press', 'read',
               'reset', 'resize', 'screenshot', 'scroll', 'type', 'wait')


def terminal(root, name):
    if sys.platform != 'linux' or name not in NAMES:
        raise Failure('mcp_installation', 'The bundled terminal connections are available on Linux.')
    runtime = Path(root) / '.state/mcp-runtime'
    program = runtime / DIRECTORY / name
    entry = program / (CLI if name == 'terminal-use' else 'quick_terminal.py')
    if not entry.is_file() or entry.resolve() != entry:
        raise Failure('mcp_installation', 'This bundled terminal is missing or has moved. Repair the Kejtu installation.')
    reads = [str(program)]
    if name == 'terminal-use':
        command = NODE
        args = [str(entry), '--shell', '/bin/bash', '--cwd', WORKING_FOLDER]
        description = 'Run interactive terminals or commands with paged output. Requires system Node.js 22.12 or newer.'
        tools = ['terminal_'+suffix for suffix in (*INTERACTIVE, 'run', 'output', 'cancel')]
        repeatable = ['terminal_list', 'terminal_read', 'terminal_wait', 'terminal_output']
    else:
        sdk = runtime / 'python'
        if not (sdk/'mcp').is_dir() or sdk.resolve() != sdk:
            raise Failure('mcp_installation', 'Quick Terminal needs Kejtu’s included MCP Python runtime. Repair the installation.')
        venv = Path(root) / '.venv'
        python = (venv/'bin/python').resolve()
        if python.is_relative_to(venv):
            reads.append(str(venv))  # Linux installers carry the matching Python outside /usr.
        command = str(python)
        reads.append(str(sdk))
        args = ['-I', '-S', '-B', str(entry), '--sdk', str(sdk), '--cwd', WORKING_FOLDER]
        description = 'Run one Bash command and read its output and exit code. Each command uses a fresh shell.'
        tools, repeatable = ['run_command'], []
    # The fixed system Node path can also be recorded on a machine which lost
    # its Node installation. Quick Terminal remains independently available.
    row = connection(dict(command=command, args=args, description=description, read_paths=reads, network=False),
                     saved=name == 'terminal-use')
    # These are the selections for the first explicit On action. Off excludes all
    # of them from the model. Re-enabling uses the operator's saved selection.
    row.update(allowed_tools=tools, repeatable_tools=repeatable)
    return row


def check_node(root, name, row):
    # Only probe the fixed system binary for our own launcher, never an imported command.
    entry = Path(root) / '.state/mcp-runtime' / DIRECTORY / 'terminal-use' / CLI
    if (sys.platform != 'linux' or name != 'terminal-use' or row.get('command') != NODE
            or row.get('args', [])[:1] != [str(entry)]):
        return
    try:
        result = subprocess.run([NODE, '--version'], cwd='/', env={'PATH': '/usr/bin:/bin'},
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=3, check=True)
        version = result.stdout.strip()
        match = re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)(?:[-+][\w.-]+)?', version)
        if match and tuple(map(int, match.groups())) >= (22, 12, 0):
            return
    except (OSError, subprocess.SubprocessError):
        match = None
    observed = ' This computer has '+version+'.' if match else ''
    raise Failure('mcp_installation', 'Terminal Use needs Node.js 22.12 or newer.'+observed+
                  ' Update system Node.js, then turn on Terminal Use in the Shelf. Quick Terminal also works without Node.js.')
