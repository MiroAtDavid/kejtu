import { request } from "$lib/api/client";
import type { Arguments, FilesStatus } from "$lib/api/wire.generated";

/** The one-step working-folder setup offered before file access is first
 *  configured: create the folder with read & write access and keep every other
 *  choice as saved. Settings → Files and the welcome window both use it. */
export function workingFolderSetup(
  status: FilesStatus,
  workspace = status.suggested_workspace,
): Arguments["files.save"] {
  return {
    expected_revision: status.revision,
    enabled: true,
    workspace,
    create_workspace: true,
    folders: status.folders.map((row) => ({ ...row })),
    servers: [...status.servers],
  };
}

/** Save file access. A failure may follow a committed save whose answer was
 *  lost, so the saved state is read back (null when it cannot be read) and
 *  handed to `readBack` before the original error is rethrown. The save itself
 *  is never repeated here. */
export async function saveFiles(
  args: Arguments["files.save"],
  readBack: (status: FilesStatus | null) => void,
): Promise<FilesStatus> {
  try {
    return await request("files.save", args);
  } catch (caught) {
    let status: FilesStatus | null = null;
    try {
      status = await request("files.status", {});
    } catch {
      status = null;
    }
    readBack(status);
    throw caught;
  }
}
