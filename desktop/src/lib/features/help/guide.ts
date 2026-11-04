// One set of shipped bytes supplies both the in-app guide and model skill.
const resources = import.meta.glob<string>(
  "../../../../../src/kejtu_next/help/[0-9]*.md",
  { eager: true, query: "?raw", import: "default" },
);

export const topics = Object.entries(resources)
  .sort(([a], [b]) => a.localeCompare(b))
  .map(([path, body]) => ({
    id: path.split("/").at(-1)!,
    title: body.split("\n")[0].replace(/^# /, ""),
    body,
  }));

export function helpPrompt(topic: string) {
  return `Read your built-in kejtu-app-help guide, especially ${topic}. Help me get started with the relevant Kejtu features and settings. Distinguish access you can verify from settings I need to check.`;
}

export type HelpDestination =
  "models" | "web" | "workspace" | "background" | "diagnostics" | "access";
export const topicNotes: Record<
  string,
  {
    summary: string;
    icon: string;
    destination?: HelpDestination;
    label?: string;
  }
> = {
  "01-start.md": {
    summary: "Connect a model and try your first conversation.",
    icon: "comment",
    destination: "models",
    label: "Models",
  },
  "02-capabilities.md": {
    summary: "See what you can do, from quick questions to project work.",
    icon: "widgets",
  },
  "03-memory.md": {
    summary: "Understand what Kejtu remembers and what you control.",
    icon: "book",
    destination: "access",
    label: "Permissions & recall",
  },
  "04-web-files-skills.md": {
    summary: "Search the web, share a folder and use a procedure.",
    icon: "search",
    destination: "web",
    label: "Web",
  },
  "05-projects-documents.md": {
    summary: "Organize a project, read sources and work on documents.",
    icon: "folder",
    destination: "workspace",
    label: "Workspace",
  },
  "06-tasks-background.md": {
    summary: "Plan your day and return to research when it is ready.",
    icon: "calendar",
    destination: "background",
    label: "Background work",
  },
  "07-settings-troubleshooting.md": {
    summary: "Find a setting or work out why something is unavailable.",
    icon: "code",
    destination: "diagnostics",
    label: "Diagnostics",
  },
  "08-privacy-limits.md": {
    summary: "Know where your information goes and how access works.",
    icon: "file",
    destination: "access",
    label: "Permissions & recall",
  },
};
