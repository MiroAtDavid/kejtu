# Projects, documents and learning

## Projects

Fresh installations show Projects and let Kejtu read and update your workspace
when you ask. In **Settings → Planning → Workspace**, **Kejtu’s help in conversation**
chooses **Read and update**, **Read only** or **Off**. Existing installations keep
their saved choices. **Use recommended setup** stages Projects on, read-and-update
access, the computer’s timezone and a 15-minute planning buffer; **Save workspace
settings** applies it. It keeps your widgets, media choices, external calendars
and Files permissions. Radio, video and external desktop-calendar access have separate switches
and start off. Manual controls remain available.
Create a **Project** for the overall work. **Sections / Modules** divide it when
useful; **Add section** creates one. Existing standalone sections remain available.
Project dates and breaks are optional under **Schedule**. Creating records does
not create folders or grant filesystem access. A task can belong to the whole
project or to a section; **Project tasks** opens the combined task list.

Share a folder in **Files**, then select it as the section's Resources folder.
Use the section sidebar and the **Overview**, **Resources**, **Practice** and
**Tasks** tabs. Paths and access should remain visible when choosing a folder.

On the first **Resources** visit, an editable **project_name.md** starter opens.
It includes the saved section name, project and Resources folder when available,
plus prompts for the purpose, background, decisions and next steps. These prompts
are not a generated factual summary. An existing matching file is opened instead
when it can be found in the shared folder.

Add your own context or use the document's **Ask Kejtu** control to develop it from
information you provide and material Kejtu can actually read. The starter is
**unsaved** until you use **Save a Markdown copy** and choose a destination.
Save it in the Resources folder to make it available through the normal file and
workspace tools. Existing permissions still apply; it is not silently loaded into
every conversation. Closing a changed starter uses the usual unsaved-edit check.

Save dialogs start in the section's Resources folder when it is available.
If no folder is assigned, **Choose or create a section folder…** lets you select
an existing shared folder or create a subfolder, then assign it to the section.
**Save elsewhere…** keeps that choice optional: the dialog starts in your system's
Documents folder if available, otherwise your home folder. **Cancel** keeps the
document open without saving. Selecting a destination does not grant new Files
permissions, and saving a copy never replaces an existing file.

## Documents

Resources keeps each opened document in its own tab. Open more files without
closing earlier ones; each tab keeps its own edits and reading position. The ×
closes that document and checks for unsaved changes. These tabs last for the
current application session, so save edits before restarting Kejtu.

**Wide view** expands reading across the window and temporarily hides the sidebar
and widgets; **Exit wide view** restores them. Use **Files** in wide view to open
another resource. With two or more documents open, **Show documents side by side**
displays a pair, including two PDFs or two editors. Choose the right document,
then drag the divider to adjust their widths. Drag a document tab along the tab
strip to reorder it, or drop it on the left or right reading area to place it
there. Moving one visible document onto the other side swaps the pair; **Swap
sides** does the same without dragging. Alt+Shift+Left/Right reorders a focused
tab with the keyboard. Selecting a visible pane makes it the target when choosing
another tab. Narrow windows show one pane until there is room for both again.

Resources can open supported files from permitted folders. PDFs open in **PDF
Craft** by default. **Read & annotate** keeps highlights, notes and drawing tools
available; **Edit text** lets you change existing selectable text and images in
the same open PDF. **All tools** exposes the other PDF tools. Switching modes
keeps your edits. **Save a PDF copy** exports the result without overwriting the
original. Scanned text is an image and cannot be edited as ordinary text in this
bundled reader. Choose **Simple reader** as the reading-only fallback. Word documents use
the integrated Word editor; Markdown has its own editor. Save/export controls and
unsaved-change prompts matter: opening, discussing or previewing a document is
not a saved edit. Complex documents may render or round-trip differently, so
check an exported result before relying on its fidelity.

Document discussion can link a retained source and page to a conversation. Kejtu
can explain or work from the exact source when its tools and grants make it
available. A PDF being visible on screen does not mean the model has read the
whole file. Scanned pages, unsupported content and missing extraction tools can
limit what it can read; it should identify the gap.

Conversational creation/editing additionally requires the relevant Files or
document connection, allowed tools and a writable destination. The embedded
reader/editor and a Craft MCP connection serve different roles. A bundled editor
does not silently install or authorize an MCP server. Ask for a concrete output
and destination, then check the saved file or export.

With the corresponding PDF tools enabled, you can ask Kejtu to edit text, add
highlights or notes, fill forms, create PDFs, rearrange or combine pages, and save
the result in a writable shared folder. Scanned PDFs may need the connected
tool's OCR support. Kejtu should check the available capabilities before saying
an operation is unavailable, and distinguish a saved result from an unsaved edit.

## Calendar and planning

The Kejtu calendar stores local commitments, project schedules and work/study
sessions. Its initial timezone follows the computer, with UTC as a fallback when
it cannot be detected. In Workspace, search the timezone dropdown or choose
**Use computer timezone**, then **Save workspace settings**. An existing saved
zone is kept on restart, including after travel. **Planning preferences** contains
the break/travel buffer. **Media & external calendars** contains the radio, video
and external-calendar switches. To connect desktop calendars, switch on
**Show desktop calendar appointments**, choose **Save and find calendars**, select
the calendars and save. The Kejtu calendar works without this external connection. Describe
preferred work hours in conversation; there is no weekly-hours settings editor.
Suggested slots are proposals until saved. Empty calendar space is not proof
that you are free. External calendar widgets are separate from this timetable
and are not automatically included in scheduling conflict checks.

## Practice

Kejtu can explain material, offer a hint, ask one source-linked question and
discuss your attempt. Ask directly for an answer when that is what you want.
Practice records and self-reported confidence do not prove mastery or constitute
official grades. Source-specific explanations should name the material used.

Optional radio, video and calendar widgets require their own local runtimes,
sources and settings. They are not enabled merely by opening Home.
