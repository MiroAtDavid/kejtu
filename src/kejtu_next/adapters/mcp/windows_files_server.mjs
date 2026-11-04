/** Windows built-in Files: application-level permissions, not process isolation.
 * The pinned upstream server retains its catalogue and ordinary file operations.
 * This adapter enforces immutable Kejtu grants before tool dispatch; recursive
 * discovery is implemented here so protected descendants are never returned.
 * It does not defend against another process racing filesystem changes.
 */
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

export const MAX_RESULT_BYTES = 2_000_000;
export const MAX_SCAN_ENTRIES = 100_000;
const MAX_DEPTH = 128;
const MAX_POLICY_BYTES = 256 * 1024;
const LOCAL_TOOLS = new Set([
  "list_directory",
  "list_directory_with_sizes",
  "directory_tree",
  "search_files",
  "list_allowed_directories",
  "read_multiple_files",
]);
export const TOOL_NAMES = Object.freeze([
  "read_file",
  "read_text_file",
  "read_media_file",
  "read_multiple_files",
  "write_file",
  "edit_file",
  "create_directory",
  "list_directory",
  "list_directory_with_sizes",
  "directory_tree",
  "move_file",
  "search_files",
  "get_file_info",
  "list_allowed_directories",
]);
const TOOL_SET = new Set(TOOL_NAMES);

export class PermissionError extends Error {}
const deny = (message = "This path is outside the permitted file access.") => {
  throw new PermissionError(message);
};
const textResult = (text, isError = false) => ({
  content: [{ type: "text", text }],
  ...(isError ? { isError: true } : { structuredContent: { content: text } }),
});
function errorResult(error) {
  if (error instanceof PermissionError || error instanceof TypeError)
    return textResult(error.message, true);
  // Do not echo native error paths: they may contain protected descendants.
  const code = /^[A-Z0-9_]+$/.test(error?.code ?? "") ? ` (${error.code})` : "";
  return textResult(
    `File operation could not complete${code}. No complete result is available.`,
    true,
  );
}
// Preserve the SDK's negotiated wire envelope (modern resultType/serverInfo,
// or legacy fields). Payload substitution must never invent or discard it.
function replacePayload(encoded, payload) {
  const { content, structuredContent, isError, ...envelope } = encoded;
  return { ...envelope, ...payload };
}
function boundResult(result) {
  return Buffer.byteLength(JSON.stringify(result), "utf8") <= MAX_RESULT_BYTES
    ? result
    : replacePayload(
        result,
        textResult(
          "The file result exceeds the 2 MB limit. Narrow the request; no complete result is available.",
          true,
        ),
      );
}
function string(value, label) {
  if (typeof value !== "string")
    throw new TypeError(`${label} must be a string.`);
  return value;
}
function strings(value, label) {
  if (!Array.isArray(value) || value.some((v) => typeof v !== "string"))
    throw new TypeError(`${label} must be an array of strings.`);
  return value;
}

/** Local drive paths only: refuse Win32 aliases before normalization. */
export function normalizeLocalPath(value, platform = process.platform) {
  string(value, "path");
  if (!value || /[\x00-\x1f]/.test(value))
    deny("An ordinary absolute local file path is required.");
  const api = platform === "win32" ? path.win32 : path.posix;
  if (platform === "win32") {
    if (!/^[a-zA-Z]:[\\/]/.test(value) || /[:<>"|?*]/.test(value.slice(2)))
      deny(
        "Only absolute local drive paths are supported; network, device and alternate-stream paths are unavailable.",
      );
    for (const part of value.slice(3).split(/[\\/]/)) {
      if (!part || part === "." || part === "..") continue;
      if (
        /[. ]$/.test(part) ||
        /^(con|prn|aux|nul|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])$/i.test(
          part.split(".")[0].trimEnd(),
        )
      )
        deny(
          "Windows device names and trailing dots or spaces are not supported.",
        );
    }
  } else if (!api.isAbsolute(value) || value.startsWith("//")) {
    deny("An ordinary absolute local file path is required.");
  }
  const normalized = api.normalize(value);
  return normalized === api.parse(normalized).root
    ? normalized
    : normalized.replace(/[\\/]$/, "");
}

/** Tests inject POSIX paths; production uses the host platform without overrides. */
export async function createPolicy(
  raw,
  { platform = process.platform, filesystem = fs } = {},
) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw))
    throw new TypeError("Files policy is missing.");
  if (
    Object.keys(raw).some(
      (key) => !["runtime", "shared_folders", "private_paths"].includes(key),
    )
  )
    throw new TypeError("Files policy has unknown fields.");
  const api = platform === "win32" ? path.win32 : path.posix;
  const normalize = (p) => normalizeLocalPath(p, platform);
  const key = (p) => (platform === "win32" ? p.toLowerCase() : p);
  const contains = (root, candidate) =>
    key(root) === key(candidate) ||
    key(candidate).startsWith(
      key(root.endsWith(api.sep) ? root : root + api.sep),
    );
  const runtime = normalize(raw.runtime);
  const privatePaths = Object.freeze(
    strings(raw.private_paths, "private_paths").map(normalize),
  );
  if (!Array.isArray(raw.shared_folders) || raw.shared_folders.length === 0)
    throw new TypeError("At least one shared folder is required.");
  const seen = new Set();
  const grants = raw.shared_folders
    .map((item) => {
      if (
        !item ||
        !["read", "write"].includes(item.access) ||
        Object.keys(item).some((field) => !["path", "access"].includes(field))
      )
        throw new TypeError("Invalid shared folder policy.");
      const p = normalize(item.path);
      if (seen.has(key(p)))
        throw new TypeError("Duplicate shared folder policy.");
      seen.add(key(p));
      if (privatePaths.some((hidden) => contains(hidden, p)))
        deny("A protected application folder cannot be shared.");
      return Object.freeze({ path: p, access: item.access });
    })
    .sort((a, b) => b.path.length - a.path.length);
  Object.freeze(grants);
  function lexical(p, access = "read") {
    if (privatePaths.some((hidden) => contains(hidden, p)))
      deny("This application folder is protected.");
    const grant = grants.find((g) => contains(g.path, p));
    if (!grant) deny();
    if (access === "write" && grant.access !== "write")
      deny("This shared folder is read-only.");
  }
  async function check(input, access = "read", { missing = false } = {}) {
    const p = normalize(input);
    lexical(p, access);
    const parts = p
      .slice(api.parse(p).root.length)
      .split(api.sep)
      .filter(Boolean);
    let current = api.parse(p).root;
    let last;
    let absent = false;
    // Walk every existing ancestor: lstat identifies symbolic links/junctions;
    // realpath additionally catches aliases or redirections changing the path.
    for (let index = -1; index < parts.length; index++) {
      if (index >= 0) current = api.join(current, parts[index]);
      try {
        last = await filesystem.lstat(current);
      } catch (error) {
        if (error.code === "ENOENT" && missing) {
          absent = true;
          break;
        }
        throw error;
      }
      if (last.isSymbolicLink())
        deny("Linked paths are not available through shared Files.");
      const real = normalize(await filesystem.realpath(current));
      if (key(real) !== key(current))
        deny(
          "The file path resolves through an unsupported alias or redirected folder.",
        );
      if (index < parts.length - 1 && !last.isDirectory())
        throw new TypeError("A path parent is not a directory.");
    }
    if (!absent) {
      if (!last.isDirectory() && !last.isFile())
        deny("Only ordinary files and directories are supported.");
      if (last.isFile() && last.nlink !== 1)
        deny(
          "Files with multiple hard links are not available through shared Files.",
        );
      lexical(normalize(await filesystem.realpath(p)), access);
    }
    return { path: p, stats: absent ? null : last };
  }
  // A missing or redirected saved root must not silently become another grant.
  for (const grant of grants) {
    const checked = await check(grant.path);
    if (!checked.stats.isDirectory())
      throw new TypeError("A shared folder is not a directory.");
  }
  function movableRoot(p) {
    if (
      privatePaths.some((hidden) => contains(p, hidden)) ||
      grants.some((g) => contains(p, g.path))
    )
      deny(
        "A folder containing protected paths or saved shared-folder roots cannot be moved.",
      );
  }
  return Object.freeze({
    runtime,
    grants,
    privatePaths,
    normalize,
    contains,
    check,
    lexical,
    movableRoot,
    api,
    filesystem,
  });
}

function scanBudget(signal, maximum = MAX_SCAN_ENTRIES) {
  let count = 0;
  return () => {
    if (signal?.aborted)
      throw new PermissionError("File operation was cancelled.");
    if (++count > maximum)
      throw new PermissionError(
        `The directory scan exceeded ${maximum} entries. Narrow the request; no complete result is available.`,
      );
  };
}
async function visibleEntries(policy, directory, tick) {
  const checked = await policy.check(directory);
  const entries = [];
  const handle = await policy.filesystem.opendir(checked.path);
  for await (const entry of handle) {
    tick();
    const target = policy.api.join(checked.path, entry.name);
    try {
      const child = await policy.check(target);
      entries.push({ name: entry.name, path: child.path, stats: child.stats });
    } catch (error) {
      // Policy omissions are intentional. IO failures must not masquerade as a
      // successful empty search; propagate them and return an explicit error.
      if (!(error instanceof PermissionError)) throw error;
    }
  }
  return entries;
}
function exclusions(args) {
  return args.excludePatterns === undefined
    ? []
    : strings(args.excludePatterns, "excludePatterns");
}
function relativeGlob(policy, root, target) {
  return policy.api.relative(root, target).split(policy.api.sep).join("/");
}

export function createGuard(
  policy,
  { minimatch, formatSize, maxScanEntries = MAX_SCAN_ENTRIES },
) {
  async function local(name, args, signal) {
    const tick = scanBudget(signal, maxScanEntries);
    if (name === "list_allowed_directories")
      return textResult(
        `Allowed directories:\n${policy.grants.map((g) => g.path).join("\n")}`,
      );
    if (name === "read_multiple_files") {
      const paths = strings(args.paths, "paths");
      if (paths.length === 0) throw new TypeError("paths must not be empty.");
      let total = 0;
      let hadError = false;
      const results = [];
      for (const input of paths) {
        tick();
        try {
          const checked = await policy.check(input);
          if (!checked.stats.isFile())
            throw new TypeError("The requested path is not a file.");
          if (checked.stats.size > MAX_RESULT_BYTES)
            throw new PermissionError(
              "This file exceeds the result limit. Read a smaller part with read_text_file.",
            );
          const body = await policy.filesystem.readFile(checked.path, "utf8");
          const result = `${input}:\n${body}\n`;
          total += Buffer.byteLength(result);
          if (total > MAX_RESULT_BYTES)
            throw new PermissionError(
              "The combined file result exceeds the 2 MB limit. Narrow the request.",
            );
          results.push(result);
        } catch (error) {
          hadError = true;
          results.push(
            `${input}: Error - ${errorResult(error).content[0].text}`,
          );
        }
      }
      return boundResult(textResult(results.join("\n---\n"), hadError));
    }
    const root = (await policy.check(args.path)).path;
    if (name === "list_directory" || name === "list_directory_with_sizes") {
      if (args.sortBy !== undefined && !["name", "size"].includes(args.sortBy))
        throw new TypeError("sortBy must be name or size.");
      const entries = await visibleEntries(policy, root, tick);
      if (name === "list_directory")
        return boundResult(
          textResult(
            entries
              .map(
                (e) =>
                  `${e.stats.isDirectory() ? "[DIR]" : "[FILE]"} ${e.name}`,
              )
              .join("\n"),
          ),
        );
      entries.sort(
        args.sortBy === "size"
          ? (a, b) => b.stats.size - a.stats.size
          : (a, b) => a.name.localeCompare(b.name),
      );
      const lines = entries.map(
        (e) =>
          `${e.stats.isDirectory() ? "[DIR]" : "[FILE]"} ${e.name.padEnd(30)} ${e.stats.isDirectory() ? "" : formatSize(e.stats.size).padStart(10)}`,
      );
      const files = entries.filter((e) => e.stats.isFile());
      lines.push(
        "",
        `Total: ${files.length} files, ${entries.length - files.length} directories`,
        `Combined size: ${formatSize(files.reduce((sum, e) => sum + e.stats.size, 0))}`,
      );
      return boundResult(textResult(lines.join("\n")));
    }
    const excluded = exclusions(args);
    if (name === "search_files") string(args.pattern, "pattern");
    const found = [];
    let resultBytes = 0;
    async function walk(current, depth) {
      if (depth > MAX_DEPTH)
        throw new PermissionError(
          "The directory nesting exceeds the scan limit. Narrow the request; no complete result is available.",
        );
      const tree = [];
      for (const entry of await visibleEntries(policy, current, tick)) {
        const relative = relativeGlob(policy, root, entry.path);
        const skip = excluded.some(
          (pattern) =>
            minimatch(relative, pattern, { dot: true }) ||
            (name === "directory_tree" &&
              !pattern.includes("*") &&
              (minimatch(relative, `**/${pattern}`, { dot: true }) ||
                minimatch(relative, `**/${pattern}/**`, { dot: true }))),
        );
        if (skip) continue;
        if (name === "search_files") {
          if (minimatch(relative, args.pattern, { dot: true })) {
            found.push(entry.path);
            resultBytes += Buffer.byteLength(entry.path) + 1;
          }
        } else resultBytes += Buffer.byteLength(entry.name) + depth * 4 + 80;
        if (resultBytes > MAX_RESULT_BYTES)
          throw new PermissionError(
            "The directory result exceeds the 2 MB limit. Narrow the request; no complete result is available.",
          );
        const node = {
          name: entry.name,
          type: entry.stats.isDirectory() ? "directory" : "file",
        };
        if (entry.stats.isDirectory())
          node.children = await walk(entry.path, depth + 1);
        if (name === "directory_tree") tree.push(node);
      }
      return tree;
    }
    const tree = await walk(root, 0);
    return boundResult(
      textResult(
        name === "directory_tree"
          ? JSON.stringify(tree, null, 2)
          : found.length
            ? found.join("\n")
            : "No matches found",
      ),
    );
  }
  async function prepare(name, args, signal) {
    if (!TOOL_SET.has(name))
      deny("This Files tool is not supported by this permission adapter.");
    if (!args || typeof args !== "object" || Array.isArray(args))
      throw new TypeError("Tool arguments must be an object.");
    if (LOCAL_TOOLS.has(name))
      return { local: () => local(name, args, signal) };
    if (name === "move_file") {
      const source = await policy.check(args.source, "write");
      const destination = await policy.check(args.destination, "write", {
        missing: true,
      });
      policy.movableRoot(source.path);
      policy.movableRoot(destination.path);
      if (source.stats.isDirectory()) {
        const tick = scanBudget(signal, maxScanEntries);
        async function inspect(current, target, depth) {
          if (depth > MAX_DEPTH)
            deny("The folder is too deeply nested to verify for moving.");
          const handle = await policy.filesystem.opendir(current);
          for await (const entry of handle) {
            tick();
            const from = await policy.check(
              policy.api.join(current, entry.name),
              "write",
            );
            const to = await policy.check(
              policy.api.join(target, entry.name),
              "write",
              { missing: true },
            );
            if (from.stats.isDirectory())
              await inspect(from.path, to.path, depth + 1);
          }
        }
        await inspect(source.path, destination.path, 0);
      }
      return {
        args: { ...args, source: source.path, destination: destination.path },
      };
    }
    const write = ["write_file", "edit_file", "create_directory"].includes(
      name,
    );
    const checked = await policy.check(args.path, write ? "write" : "read", {
      missing: name === "write_file" || name === "create_directory",
    });
    if (
      checked.stats?.isFile() &&
      (name === "read_media_file" ||
        ((name === "read_file" || name === "read_text_file") &&
          args.head === undefined &&
          args.tail === undefined))
    ) {
      const size =
        name === "read_media_file"
          ? Math.ceil(checked.stats.size / 3) * 4
          : checked.stats.size;
      if (size > MAX_RESULT_BYTES)
        deny(
          "This file exceeds the result limit. Read a smaller part with read_text_file.",
        );
    }
    return { args: { ...args, path: checked.path } };
  }
  return Object.freeze({ prepare, local });
}

/** Public Transport middleware; no dependency monkeypatches or private fields. */
export class GuardTransport {
  constructor(inner, guard) {
    this.inner = inner;
    this.guard = guard;
    this.pending = new Map();
    this.closed = false;
  }
  async start() {
    this.inner.onmessage = (message, extra) => {
      void this.receive(message, extra).catch((error) => this.onerror?.(error));
    };
    this.inner.onerror = (error) => this.onerror?.(error);
    this.inner.onclose = () => {
      this.closed = true;
      for (const p of this.pending.values()) p.abort.abort();
      this.pending.clear();
      this.onclose?.();
    };
    return this.inner.start();
  }
  setProtocolVersion(version) {
    this.inner.setProtocolVersion?.(version);
  }
  async close() {
    this.closed = true;
    for (const p of this.pending.values()) p.abort.abort();
    this.pending.clear();
    return this.inner.close();
  }
  async receive(message, extra) {
    if (this.closed) return;
    if (message.method === "notifications/cancelled")
      this.pending.get(message.params?.requestId)?.abort.abort();
    // The host's saved Files policy is the only authority. Client Roots must not
    // replace it or accidentally shrink the upstream allowed-directory list.
    if (message.method === "notifications/roots/list_changed") return;
    if (
      message.method === "initialize" &&
      message.params?.capabilities?.roots
    ) {
      const capabilities = { ...message.params.capabilities };
      delete capabilities.roots;
      message = { ...message, params: { ...message.params, capabilities } };
    }
    if (message.method !== "tools/call" || message.id === undefined) {
      this.onmessage?.(message, extra);
      return;
    }
    if (this.pending.has(message.id)) {
      await this.inner.send({
        jsonrpc: "2.0",
        id: message.id,
        error: { code: -32600, message: "Duplicate active request id." },
      });
      return;
    }
    const pending = { abort: new AbortController(), local: null };
    this.pending.set(message.id, pending);
    try {
      const prepared = await this.guard.prepare(
        message.params?.name,
        message.params?.arguments ?? {},
        pending.abort.signal,
      );
      if (pending.abort.signal.aborted || this.closed) {
        this.pending.delete(message.id);
        return;
      }
      pending.local = prepared.local;
      // Locally handled tools first pass a harmless upstream call through the
      // SDK. Its successful reply proves per-request protocol validation before
      // traversal runs; malformed modern envelopes therefore perform no IO.
      const params = prepared.local
        ? { ...message.params, name: "list_allowed_directories", arguments: {} }
        : { ...message.params, arguments: prepared.args };
      this.onmessage?.({ ...message, params }, extra);
    } catch (error) {
      if (this.closed || pending.abort.signal.aborted) {
        this.pending.delete(message.id);
        return;
      }
      // Policy failures are tool errors, but still need SDK protocol validation
      // and the negotiated result envelope. Use the same harmless dispatch as
      // local reads; never return an unencoded handcrafted tool result.
      pending.local = async () => errorResult(error);
      this.onmessage?.(
        {
          ...message,
          params: {
            ...message.params,
            name: "list_allowed_directories",
            arguments: {},
          },
        },
        extra,
      );
    }
  }
  async send(message, options) {
    const pending = this.pending.get(message.id);
    if (pending && ("result" in message || "error" in message)) {
      try {
        if (
          pending.local &&
          message.result &&
          !message.result.isError &&
          !pending.abort.signal.aborted
        ) {
          message = {
            ...message,
            result: replacePayload(message.result, await pending.local()),
          };
        }
      } catch (error) {
        message = {
          jsonrpc: "2.0",
          id: message.id,
          result: replacePayload(message.result, errorResult(error)),
        };
      }
      this.pending.delete(message.id);
      if (pending.abort.signal.aborted || this.closed) return;
      if (message.result)
        message = { ...message, result: boundResult(message.result) };
    }
    if (!this.closed) return this.inner.send(message, options);
  }
}

export async function loadRuntime(runtime) {
  const require = createRequire(path.join(runtime, "package.json"));
  const resolveHere = (name) => {
    const resolved = require.resolve(name);
    const relative = path.relative(
      path.join(runtime, "node_modules"),
      resolved,
    );
    if (
      relative.startsWith(".." + path.sep) ||
      relative === ".." ||
      path.isAbsolute(relative)
    )
      throw new Error(
        "A Files dependency resolved outside its packaged runtime.",
      );
    return resolved;
  };
  const packageFile = resolveHere(
    "@modelcontextprotocol/server-filesystem/package.json",
  );
  const info = JSON.parse(await fs.readFile(packageFile, "utf8"));
  if (info.version !== "2.0.1")
    throw new Error("The Files runtime version does not match this adapter.");
  const packageRoot = path.dirname(packageFile);
  const [server, library] = await Promise.all(
    ["server.js", "lib.js"].map(
      (name) =>
        import(pathToFileURL(path.join(packageRoot, "dist", name)).href),
    ),
  );
  // Public package exports resolve from this exact packaged runtime, never cwd.
  const sdkRoot = path.dirname(
    path.dirname(resolveHere("@modelcontextprotocol/server/stdio")),
  );
  const sdkInfo = JSON.parse(
    await fs.readFile(path.join(sdkRoot, "package.json"), "utf8"),
  );
  const stdioExport = sdkInfo.exports?.["./stdio"]?.import?.default;
  if (
    sdkInfo.name !== "@modelcontextprotocol/server" ||
    sdkInfo.version !== "2.4.0" ||
    typeof stdioExport !== "string" ||
    !stdioExport.startsWith("./dist/")
  )
    throw new Error("The Files SDK export does not match this adapter.");
  const transport = await import(
    pathToFileURL(path.join(sdkRoot, stdioExport)).href
  );
  const { minimatch } = require(resolveHere("minimatch"));
  return { ...server, ...transport, minimatch, formatSize: library.formatSize };
}
export async function main(
  raw,
  {
    stdin = process.stdin,
    stdout = process.stdout,
    platform = process.platform,
  } = {},
) {
  const policy = await createPolicy(raw, { platform });
  const runtime = await loadRuntime(policy.runtime);
  const guard = createGuard(policy, runtime);
  return runtime.serveStdio(
    ({ era }) => {
      const server = runtime.createServer(
        policy.grants.map((g) => g.path),
        { era },
      );
      const connect = server.connect.bind(server);
      server.connect = (transport) =>
        connect(new GuardTransport(transport, guard));
      return server;
    },
    {
      transport: new runtime.StdioServerTransport(stdin, stdout, {
        maxBufferSize: MAX_RESULT_BYTES,
      }),
      onerror: () => {
        console.error("Files transport failed.");
        process.exitCode = 1;
      },
    },
  );
}
if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const serialized = process.env.KEJTU_FILES_POLICY;
  delete process.env.KEJTU_FILES_POLICY;
  try {
    if (!serialized || Buffer.byteLength(serialized) > MAX_POLICY_BYTES)
      throw new Error("Missing or oversized Files policy.");
    await main(JSON.parse(serialized));
  } catch {
    console.error(
      "Shared Files could not start with the supplied permissions and runtime.",
    );
    process.exitCode = 1;
  }
}
