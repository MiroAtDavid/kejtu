"""Shared staging and pinned SDK setup for optional MCP runtimes."""
import importlib.util
import json
import re
from pathlib import Path
import subprocess

# Same checkout-anchored load as the source installer: publication is owned by
# the shared, stdlib-only artifact module, whatever environment runs this tool.
_spec = importlib.util.spec_from_file_location(
    'kejtu_artifacts', Path(__file__).resolve().parents[1] / 'src/kejtu_next/adapters/artifacts.py')
_artifacts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_artifacts)


def run(arguments, *, env=None, capture=False):
    return subprocess.run([str(arg) for arg in arguments], check=True, env=env,
                          text=True, stdout=subprocess.PIPE if capture else None)


def install_directory(destination, populate):
    """Publish only a completed new component; never replace an installed one."""
    def prepare(temporary):
        staging = temporary / 'component'
        staging.mkdir(mode=0o700)
        populate(staging)
        return staging
    _artifacts.publish(destination, prepare)


def check_sdk(python, sdk, lock):
    expected = {}
    for line in lock.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^ ;]+)', line)
        if not match:
            raise ValueError('The MCP dependency lock must contain exact versions.')
        name = re.sub(r'[-_.]+', '-', match[1]).lower()
        if name in expected:
            raise ValueError('The MCP dependency lock contains a duplicate package.')
        expected[name] = match[2]
    # Isolated/no-site mode prevents the application venv from masking a missing
    # or stale worker dependency. Inspect only the worker's own distribution data.
    script = """
import sys, json, re
from importlib.metadata import distributions
from pathlib import Path
root = Path(sys.argv[1]).resolve()
expected = json.loads(sys.argv[2])
actual = {}
for distribution in distributions(path=[str(root)]):
    name = distribution.metadata.get('Name')
    if not name:
        continue
    name = re.sub(r'[-_.]+', '-', name).lower()
    if name in actual:
        sys.exit('Duplicate MCP runtime distribution: ' + name)
    actual[name] = distribution.version
bad = [f'{name}: expected {version}, found {actual.get(name, "missing")}'
       for name, version in expected.items() if actual.get(name) != version]
if bad:
    sys.exit('MCP runtime does not match this release; preserved without replacement: ' + '; '.join(bad))
sys.path.insert(0, str(root))
import mcp, jsonschema
if not all(Path(module.__file__).resolve().is_relative_to(root) for module in (mcp, jsonschema)):
    sys.exit('MCP imports escaped the installed runtime')
"""
    run([python, '-I', '-S', '-B', '-c', script, sdk, json.dumps(expected)])


def install_sdk(root):
    python = root / '.venv/bin/python'
    sdk = root / '.state/mcp-runtime/python'
    lock = root / 'ops/mcp/requirements.lock'
    print('Checking the pinned MCP Python SDK…', flush=True)

    def populate(target):
        run([python, '-m', 'pip', 'install', '--disable-pip-version-check',
             '--target', target, '-r', lock])
        check_sdk(python, target, lock)

    install_directory(sdk, populate)
    check_sdk(python, sdk, lock)
