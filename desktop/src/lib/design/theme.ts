import { derived, get, readonly, writable } from "svelte/store";

export type ThemeChoice = "system" | "light" | "dark";
export type PaletteChoice =
  "default" | "kejtu" | "slate" | "sage" | "lavender" | "sand" | "rose";
export type DensityChoice = "comfortable" | "compact";
const STORAGE_KEY = "kejtu-next.appearance.theme.v1";
const PALETTE_KEY = "kejtu-next.appearance.palette.v1";
const DENSITY_KEY = "kejtu-next.appearance.density.v1";
const CHAT_KEY = "kejtu-next.appearance.chat.v1";
export type ChatAppearance = {
  userThumbnail: boolean;
  kejtuThumbnail: boolean;
  rotateKejtu: boolean;
};
const DEFAULT_CHAT: ChatAppearance = {
  userThumbnail: true,
  kejtuThumbnail: true,
  rotateKejtu: true,
};
const chat = writable<ChatAppearance>({ ...DEFAULT_CHAT });
export const chatAppearance = readonly(chat);
// A hidden thumbnail cannot carry activity. Use the same decision in the reply
// and its collapsed/other-conversation fallback so work never loses its indicator.
export const rotatingKejtu = derived(
  chat,
  (value) => value.kejtuThumbnail && value.rotateKejtu,
);
const choice = writable<ThemeChoice>("system");
export const themeChoice = readonly(choice);
const palette = writable<PaletteChoice>("default");
const density = writable<DensityChoice>("compact");
export const paletteChoice = readonly(palette);
export const densityChoice = readonly(density);

function apply(value: ThemeChoice): void {
  choice.set(value);
  if (value === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = value;
}

export function restoreTheme(): void {
  let value: ThemeChoice = "system";
  let paletteValue: PaletteChoice = "default";
  let densityValue: DensityChoice = "compact";
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark") value = saved;
    const savedPalette = window.localStorage.getItem(PALETTE_KEY);
    if (
      savedPalette === "kejtu" ||
      savedPalette === "slate" ||
      savedPalette === "sage" ||
      savedPalette === "lavender" ||
      savedPalette === "sand" ||
      savedPalette === "rose"
    )
      paletteValue = savedPalette;
    if (window.localStorage.getItem(DENSITY_KEY) === "comfortable")
      densityValue = "comfortable";
  } catch {
    // Unavailable storage follows the system; appearance never blocks startup.
  }
  apply(value);
  applyPalette(paletteValue);
  applyDensity(densityValue);
  let chatValue = { ...DEFAULT_CHAT };
  try {
    const saved = JSON.parse(window.localStorage.getItem(CHAT_KEY) ?? "null");
    if (saved && typeof saved === "object" && !Array.isArray(saved)) {
      for (const key of Object.keys(chatValue) as (keyof ChatAppearance)[]) {
        if (typeof saved[key] === "boolean") chatValue[key] = saved[key];
      }
    }
  } catch {
    // Missing, malformed or unavailable storage keeps the existing appearance.
  }
  chat.set(chatValue);
}

function remember(key: string, value: string): boolean {
  try {
    window.localStorage.setItem(key, value);
    return true;
  } catch {
    // A preference still works for this window if it cannot be remembered.
    return false;
  }
}

export function setTheme(value: ThemeChoice): boolean {
  apply(value);
  return remember(STORAGE_KEY, value);
}

function applyPalette(value: PaletteChoice): void {
  palette.set(value);
  document.documentElement.dataset.palette = value;
}
export function setPalette(value: PaletteChoice): boolean {
  applyPalette(value);
  return remember(PALETTE_KEY, value);
}
function applyDensity(value: DensityChoice): void {
  density.set(value);
  document.documentElement.dataset.density = value;
}
export function setDensity(value: DensityChoice): boolean {
  applyDensity(value);
  return remember(DENSITY_KEY, value);
}

export function setChatAppearance(
  key: keyof ChatAppearance,
  enabled: boolean,
): boolean {
  const value = { ...get(chat), [key]: enabled };
  chat.set(value);
  return remember(CHAT_KEY, JSON.stringify(value));
}
