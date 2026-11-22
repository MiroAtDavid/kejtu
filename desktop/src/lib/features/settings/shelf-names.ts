import { knownConnectionName } from "./connections";

/**
 * Names on the Shelf follow one rule. A package from the Shelf catalogue keeps
 * its catalogue title, and a tool that comes with Kejtu keeps its fixed name.
 * A part named like its package, or a package's only connection, takes the
 * package's title. Every other name reads as words: "lab-writing" becomes
 * "Lab writing". Only the display changes; the exact name stays the identity
 * that is saved, matched and shown on the item's own page.
 */
export function readableName(name: string): string {
  const words = name.replace(/[-_\s]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : name;
}

/** What a part's package contributes to its name. */
export type NameOwner = {
  name: string;
  title: string;
  soleConnection?: string;
};

export function partTitle(
  kind: "skill" | "connection",
  name: string,
  owner?: NameOwner,
): string {
  const known = kind === "connection" ? knownConnectionName(name) : undefined;
  if (known) return known;
  if (
    owner &&
    (name === owner.name ||
      (kind === "connection" && name === owner.soleConnection))
  )
    return owner.title;
  return readableName(name);
}
