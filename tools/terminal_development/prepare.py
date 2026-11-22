"""Apply Kejtu’s command tools to an exact upstream terminal-use installation."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

EXPECTED = {
    'dist/index.js': '6b0c8216d22df6cc7c6b0ce56abea1f2eab61980f6db737d2e1bcb26031cf1e6',
    'dist/mcp/tools/index.js': '85cff7b37ddf75f6f9b05a91e7dc54118e6abaaf2dbde0682c61b2007a9b415c',
}


def adapt(directory):
    """Patch a newly staged npm package. Its existing MIT and font notices stay in place."""
    destination = Path(directory)
    package = json.loads((destination/'package.json').read_text())
    if package['name'] != 'terminal-use' or package['version'] != '0.2.1':
        raise ValueError('The adaptation requires terminal-use 0.2.1.')
    for relative, expected in EXPECTED.items():
        if hashlib.sha256((destination/relative).read_bytes()).hexdigest() != expected:
            raise ValueError('Upstream source differs: '+relative)
    shutil.copyfile(Path(__file__).with_name('commands.js'), destination/'dist/mcp/tools/commands.js')
    p = destination/'dist/mcp/tools/index.js'
    p.write_text("import { run, output, cancel } from './commands.js';\n" +
        p.read_text().replace('export const TOOLS = [', 'export const TOOLS = [\n    run, output, cancel,', 1))
    p = destination/'dist/index.js'
    original = 'return { buildServer, dispose: () => context.dispose() };'
    assert original in p.read_text()
    p.write_text("import { disposeCommands } from './mcp/tools/commands.js';\n" + p.read_text().replace(
        original, 'return { buildServer, dispose: () => { disposeCommands(context); context.dispose(); } };', 1))
    package['version'] = '0.2.1-kejtu.1'
    (destination/'package.json').write_text(json.dumps(package, indent=2)+'\n')
    return package['version']


def prepare(source, destination):
    """Retain the standalone developer trial with its existing dependency tree."""
    source, destination = Path(source).resolve(), Path(destination)
    if destination.exists():
        raise ValueError('Destination exists; do not overwrite an existing install.')
    shutil.copytree(source, destination)
    try:
        version = adapt(destination)
    except Exception:
        shutil.rmtree(destination)
        raise
    # Reuse the exact already-installed dependencies; no download or lifecycle scripts.
    for sibling in source.parent.iterdir():
        target = destination.parent/sibling.name
        if sibling.name != source.name and not target.exists():
            target.symlink_to(sibling, target_is_directory=sibling.is_dir())
    return {'upstream':str(source),'prepared':str(destination),'upstream_version':'0.2.1',
            'version':version,'upstream_inputs':EXPECTED,
            'commands_sha256':hashlib.sha256((destination/'dist/mcp/tools/commands.js').read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source');parser.add_argument('destination')
    args=parser.parse_args()
    print(json.dumps(prepare(args.source,args.destination),indent=2))
