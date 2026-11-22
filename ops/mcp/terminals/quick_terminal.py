"""Kejtu's single-command MCP server, launched inside the existing Linux tool sandbox."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid


def run_command(command: str, cwd: str | None = None, timeout_seconds: int = 30,
                max_output_bytes: int = 6000) -> str:
    """Run a Bash command and return its exit code and combined stdout/stderr.

    Each call starts a fresh shell. Use cwd to work in a shared folder. The
    result includes the full log path for output larger than the requested preview.
    """
    if not command.strip() or len(command) > 64000:
        raise ValueError('Supply a nonempty command of at most 64000 characters.')
    if not 1 <= timeout_seconds <= 45 or not 256 <= max_output_bytes <= 16000:
        raise ValueError('Use a timeout of 1–45 seconds and an output preview of 256–16000 bytes.')
    location = Path(cwd or '.').resolve(strict=True)
    if not location.is_dir():
        raise ValueError('The working directory must be a folder.')
    # This mount belongs only to this MCP connection and also works with read-only shares.
    logs = Path('/mcp-cache/terminal-logs')
    logs.mkdir(exist_ok=True)
    log_path = logs / (uuid.uuid4().hex + '.log')
    start = time.monotonic()
    timed_out = False
    with log_path.open('xb') as stream:
        child = subprocess.Popen(['/bin/bash', '--noprofile', '--norc', '-c', command],
                                 cwd=location, stdin=subprocess.DEVNULL, stdout=stream,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        try:
            child.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
    size = log_path.stat().st_size
    with log_path.open('rb') as stream:
        if size <= max_output_bytes:
            preview = stream.read().decode('utf-8', errors='replace')
        else:
            first = max_output_bytes // 2
            head = stream.read(first)
            stream.seek(first - max_output_bytes, os.SEEK_END)
            preview = (head.decode('utf-8', errors='replace') +
                       '\n[Preview truncated. Full output is in log_path.]\n' +
                       stream.read().decode('utf-8', errors='replace'))
    result = dict(status='timed_out' if timed_out else 'completed', exit_code=child.returncode,
                  duration_seconds=round(time.monotonic()-start, 3), output=preview,
                  output_bytes=size, truncated=size > max_output_bytes, log_path=str(log_path))
    log_path.with_suffix('.json').write_text(json.dumps(dict(command=command, cwd=str(location), **result),
                                                        ensure_ascii=False), encoding='utf-8')
    return json.dumps(result, ensure_ascii=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--sdk', required=True, type=Path)
    parser.add_argument('--cwd', default='.')
    args = parser.parse_args()
    # -I -S keeps Python isolated. Import the same pinned SDK as the existing MCP worker.
    sys.path.insert(0, str(args.sdk.resolve(strict=True)))
    from mcp.server.mcpserver import MCPServer
    os.chdir(args.cwd)
    server = MCPServer('quick-terminal')
    server.tool(structured_output=False)(run_command)
    server.run(transport='stdio')
