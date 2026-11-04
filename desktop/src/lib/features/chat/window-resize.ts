import type { Window as TauriWindow } from "@tauri-apps/api/window";

type ResizeDirection = Parameters<TauriWindow["startResizeDragging"]>[0];

/** The outer five CSS pixels resize a frameless window; content stays interactive. */
export function windowResizeDirection(
  x: number,
  y: number,
  width: number,
  height: number,
): ResizeDirection | undefined {
  if (x < 0 || y < 0 || x >= width || y >= height) return;
  const north = y < 5;
  const south = y >= height - 5;
  const west = x < 5;
  const east = x >= width - 5;
  if (north && west) return "NorthWest";
  if (north && east) return "NorthEast";
  if (south && west) return "SouthWest";
  if (south && east) return "SouthEast";
  if (north) return "North";
  if (south) return "South";
  if (west) return "West";
  if (east) return "East";
}
