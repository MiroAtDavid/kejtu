import type { Widget } from "./workspace";

export const widgetDefinitions: Record<
  Widget["kind"],
  { title: string; description: string }
> = {
  radio: { title: "Radio", description: "A station beside your writing." },
  note: {
    title: "Notebook note",
    description: "Keep an original note close at hand.",
  },
  calendar: {
    title: "Upcoming",
    description: "Appointments from your selected calendars.",
  },
  bookmarks: {
    title: "Bookmarks",
    description: "A few useful places on the web.",
  },
  university: {
    title: "Projects",
    description: "Your projects, sections and calendar.",
  },
  tasks: {
    title: "Personal tasks",
    description: "The things you are working toward.",
  },
};
