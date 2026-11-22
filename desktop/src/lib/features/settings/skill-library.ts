import { request } from "$lib/api/client";
import type { SkillStatus } from "$lib/api/wire.generated";

/** Read one complete library revision, including skills past the first native page. */
export async function readSkillLibrary(
  first?: SkillStatus,
): Promise<SkillStatus> {
  let page = first ?? (await request("skills.status", { offset: 0 }));
  const { revision, total } = page;
  const items = [...page.items];
  while (page.next_offset !== null) {
    if (
      !page.items.length ||
      page.next_offset !== items.length ||
      items.length >= total
    )
      throw new Error("The skill library could not be read completely.");
    page = await request("skills.status", { offset: page.next_offset });
    if (page.revision !== revision || page.total !== total)
      throw new Error(
        "Skills changed while reading. Refresh to read them again.",
      );
    items.push(...page.items);
  }
  if (
    items.length !== total ||
    new Set(items.map((item) => item.name)).size !== total
  )
    throw new Error("The skill library could not be read completely.");
  return { revision, total, items, next_offset: null };
}
