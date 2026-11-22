// Kejtu's bounded command-output tools for terminal-use 0.2.1 (MIT).
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { closeSync, existsSync, mkdirSync, openSync, readSync, statSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { z } from 'zod';
import { defaultShell, shellArgs } from '../../pty/spawn.js';
import { defineTool } from '../ToolDefinition.js';

const pools = new WeakMap();
const wait = ms => new Promise(resolveWait => setTimeout(resolveWait, ms));
const outputSchema = {
    max_output_bytes: z.number().int().min(256).max(16000).optional()
        .describe('Maximum bytes in this output page, 256..16000; default 6000. Full output remains in log_path.'),
    wait_ms: z.number().int().min(0).max(10000).optional()
        .describe('Wait at most this many milliseconds for completion, 0..10000; default 1000 for run and 0 for output.')
};

function pool(context) {
    if (!pools.has(context)) pools.set(context, new Map());
    return pools.get(context);
}

function kill(record) {
    const pid = record.child?.pid;
    if (!Number.isInteger(pid) || pid <= 1 || pid === process.pid) return;
    try { process.kill(-pid, 'SIGKILL'); }
    catch (error) { if (error.code !== 'ESRCH') throw error; }
}

function metadata(record) {
    return {
        command_id: record.id, status: record.status, exit_code: record.exitCode,
        signal: record.signal, cwd: record.cwd, log_path: record.log,
        duration_seconds: ((record.finished ?? Date.now()) - record.started) / 1000
    };
}

function persist(record) {
    writeFileSync(record.log + '.json', JSON.stringify({
        ...metadata(record), command: record.command, timeout_seconds: record.timeout
    }, null, 2));
}

async function outputPage(record, args) {
    if (record.status === 'running' && (args.wait_ms ?? 0) > 0)
        await Promise.race([record.done, wait(args.wait_ms)]);
    const size = statSync(record.log).size;
    const offset = args.offset ?? 0;
    if (offset > size) throw new Error('offset exceeds the saved output size; no command was repeated.');
    const bytes = Math.min(size - offset, args.max_output_bytes ?? 6000);
    const data = Buffer.alloc(bytes);
    const fd = openSync(record.log, 'r');
    let count;
    try { count = readSync(fd, data, 0, bytes, offset); }
    finally { closeSync(fd); }
    const end = offset + count;
    return {
        ...metadata(record), output: data.subarray(0, count).toString('utf8'),
        offset, end, output_bytes: size, truncated: end < size,
        next_offset: end < size || record.status === 'running' ? end : null,
        encoding: 'UTF-8 display; exact original bytes retained in log_path'
    };
}

export const run = defineTool({
    name: 'terminal_run', title: 'Run a command', needsSession: false,
    description: 'Run an ordinary shell command without a terminal screen. Returns output, status and exit code, '
        + 'or a command_id for a still-running command. Use terminal_output with next_offset to read new output. '
        + 'Each command starts a fresh shell. No automatic retry.',
    schema: {
        command: z.string().min(1).max(64000).describe('Shell command; pipelines and coherent command batches are supported.'),
        cwd: z.string().min(1).max(4096).optional().describe('Working directory inside granted folders; defaults to the configured terminal directory.'),
        timeout_seconds: z.number().int().min(1).max(45).optional().describe('Hard command lifetime, 1..45 seconds; default 30. Expiry stops its process group.'),
        ...outputSchema
    },
    annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: true },
    handler: async (request, response, context) => {
        if (process.platform !== 'linux') throw new Error('These command-output tools are supported on Linux only.');
        const records = pool(context);
        if (records.size >= 256) {
            const oldest = [...records.values()].find(record => record.status !== 'running');
            if (!oldest) throw new Error('Command capacity is full; inspect or cancel existing commands.');
            records.delete(oldest.id); // Full output/metadata files remain available.
        }
        const args = request.params;
        const cwd = resolve(args.cwd ?? context.defaults.cwd ?? process.cwd());
        const directory = existsSync('/mcp-cache') ? '/mcp-cache/terminal-logs' : join(cwd, '.state', 'terminal-logs');
        mkdirSync(directory, { recursive: true });
        const id = randomUUID().replaceAll('-', '');
        const log = join(directory, id + '.log');
        const fd = openSync(log, 'wx');
        const record = { id, cwd, log, command: args.command, timeout: args.timeout_seconds ?? 30,
            status: 'running', exitCode: null, signal: null, started: Date.now() };
        records.set(id, record);
        let settle;
        record.done = new Promise(resolveDone => { settle = resolveDone; });
        const shell = context.defaults.shell ?? defaultShell();
        let finished = false;
        function finish(code, signal, error) {
            if (finished) return;
            finished = true;
            clearTimeout(record.timer);
            kill(record); // Background descendants are not detached jobs.
            closeSync(fd);
            record.exitCode = code;
            record.signal = signal;
            record.status = record.ending ?? (error ? 'failed' : 'completed');
            record.finished = Date.now();
            if (error) record.error = String(error);
            persist(record);
            settle();
        }
        try {
            record.child = spawn(shell, shellArgs(shell, args.command, false), {
                cwd, env: process.env, detached: true, stdio: ['ignore', fd, fd]
            });
            record.child.once('error', error => finish(null, null, error));
            record.child.once('close', (code, signal) => finish(code, signal));
            record.timer = setTimeout(() => {
                record.ending = 'timed_out';
                kill(record);
            }, record.timeout * 1000);
            persist(record);
        } catch (error) {
            finish(null, null, error);
        }
        response.appendLine(JSON.stringify(await outputPage(record, { ...args, wait_ms: args.wait_ms ?? 1000 })));
    }
});

export const output = defineTool({
    name: 'terminal_output', title: 'Read command output', needsSession: false,
    description: 'Read a saved command result or only its new output using next_offset. With no command_id, list '
        + 'this conversation terminal\'s commands and status. Reading never restarts a command. An expired ID is not proof of success.',
    schema: {
        command_id: z.string().regex(/^[0-9a-f]{32}$/).optional(),
        offset: z.number().int().min(0).optional().describe('Byte offset; use the previous next_offset to avoid repeating output.'),
        ...outputSchema
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    handler: async (request, response, context) => {
        const records = pool(context);
        if (!request.params.command_id) {
            response.appendLine(JSON.stringify({ commands: [...records.values()].map(metadata) }));
            return;
        }
        const record = records.get(request.params.command_id);
        if (!record) throw new Error('Command ID is unavailable in this terminal. Inspect retained files; do not repeat an uncertain action.');
        response.appendLine(JSON.stringify(await outputPage(record, request.params)));
    }
});

export const cancel = defineTool({
    name: 'terminal_cancel', title: 'Cancel command', needsSession: false,
    description: 'Stop a direct command and its descendants. With no command_id, stop all direct commands in '
        + 'this conversation terminal. Completed commands are never restarted. Interactive sessions use terminal_destroy or the app\'s Stop.',
    schema: { command_id: z.string().regex(/^[0-9a-f]{32}$/).optional() },
    annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: true, openWorldHint: false },
    handler: async (request, response, context) => {
        const records = pool(context);
        const targets = request.params.command_id ? [records.get(request.params.command_id)] : [...records.values()];
        if (targets.some(record => !record)) throw new Error('Command ID is unavailable; nothing was restarted.');
        for (const record of targets) {
            if (record.status === 'running') {
                record.ending = 'cancelled';
                kill(record);
                await record.done;
            }
        }
        response.appendLine(JSON.stringify({ commands: targets.map(metadata) }));
    }
});

export function disposeCommands(context) {
    for (const record of pool(context).values()) {
        if (record.status === 'running') {
            record.ending = 'cancelled';
            kill(record);
        }
    }
}
