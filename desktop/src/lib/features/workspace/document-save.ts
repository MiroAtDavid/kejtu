/** The verified source or successful Save As receipt supplies this chooser hint.
 * It grants no access: the native dialog still checks the directory and asks
 * the user for a destination. New documents use the section-folder flow. */
export function documentDirectory(
  path: string | null | undefined,
): string | null {
  if (!path || path.includes("\0")) return null;
  const windows = /^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+\\)/.test(path);
  if (!windows && !path.startsWith("/")) return null;
  const separator = windows
    ? Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"))
    : path.lastIndexOf("/");
  if (separator < 0 || separator === path.length - 1) return null;
  if (separator === 0) return "/";
  if (windows && separator === 2) return path.slice(0, 3);
  return path.slice(0, separator);
}
