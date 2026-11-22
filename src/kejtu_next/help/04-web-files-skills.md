# Web, files, connections and skills

## Search and page reading

New desktop installations start with **Web access on** and **Tavily** selected.
Tavily supports free, rate-limited search without a key; add an account key for
your account's quota. Open **Settings → Everyday → Web** to change the
provider or turn access off. Supported connections include SearxNG, Brave and Tavily. Providers
have their own endpoint/key requirements; enter credentials in the panel.
Reading pages uses the installed extraction/browser runtime. A configured search
provider does not guarantee every page is readable: login, blocking, scripts,
timeouts and unsupported content can prevent a complete read.

Search snippets are leads, not the full page. Kejtu can read sources and retain
results with source identities for the current work. It should say when coverage
is partial and distinguish verified page contents from an inference.

## Interactive browsing and other MCP connections

The standard setup installs and enables a temporary browser connection.
**Shelf → Installed → Connections** manages it and other MCP servers. MCP is a way
to expose another application's tools to Kejtu. Configure/install the server,
inspect its actual tools, select allowed tools and grant the required network and
folder access. Enabled web access is additionally required for connections marked
as needing the web. Importing a connection is not a successful connection check.

A browser connection can navigate and interact with pages using its available
tools. It is distinct from built-in search/page reading. No login, email sending,
account access or arbitrary desktop control is supplied merely by installing
Kejtu. External effects depend on the actual connection and your permissions.

## Shared folders

In **Files**, choose a new Kejtu working folder, an existing working folder or
no working folder. Add other shared folders with **Read only** or **Read & write**
access. **Save changes** applies the choices; opening the panel shares nothing.
Use **Change…** to change an assigned working folder. The shared file access
switch turns access off immediately, even during a reply. Turning it back on
uses **Save changes** here. The module editor’s Browse/Create folder actions can
also turn it on while sharing the chosen folder for reading and writing.
Folder edits are kept when switching access off.
The working folder is writable. Subfolders inherit access unless a more specific
share overrides it. Expand **Tools using these folders** to choose which local
connections may use them. Separate read-only connection grants remain available
when shared access is off; manage those in Connections → Advanced.

The installed Files server supports listing, searching, reading, creating,
editing and moving files/directories. It does not expose delete/trash. Removing
a share stops access after Save; it does not delete the files. A project folder
assignment does not grant access by itself. Missing drives and revoked access
must produce an explicit unavailable result.

On Linux, share Home read-only and choose Documents or another separate folder
for writing. Writable access cannot include application code, Python packages,
or desktop/shell startup locations such as `.local`, `.config` and `.ssh`.
This also applies when Kejtu is installed outside Home. A writable folder that
could replace any symlinked import/startup component, or could create a directory
named by an existing Python `.pth` path entry, is refused. An unsafe saved choice
stops the connection; it does not silently change your permissions. Python startup
files that cannot be inspected or decoded also stop the connection. `.pth` reads
are limited to one MiB per file and eight MiB in total; oversized files are refused,
not partially interpreted. Executable `.pth` hooks are refused, even when a
hook's condition is currently false. The supported runtime's setuptools 80.9.0
packaging hook is permitted only when its exact contents, shim code, package
metadata and import location match the verified release. A matching filename or
version alone is not enough. Other executable hooks, including benign custom
hooks, remain unsupported. Kejtu never evaluates, deletes or rewrites a hook to
make a connection work.

Before a local tool starts, Kejtu checks writable folders and its private cache.
Hard links are allowed only when all their file names stay within the writable
view; cache links must stay within that connection’s cache. Links to other files
need independent copies or read-only access. Read-only/private subfolders are
excluded; deeper explicit writable grants are checked directly without listing
read-only siblings. Directory entries are checked incrementally, with a limit of
100,000 distinct writable names and a cooperative five-second budget including
Python startup inspection. An individual filesystem operation can take longer.
Launch checks run off the request event loop. Cancelling during preflight prevents
worker launch, but a blocked filesystem call may continue until it returns. If a check cannot
finish, choose a smaller accessible folder or read-only access. Kejtu does not
delete or automatically rebuild files or caches to make a connection work.

The Windows test package includes shared Files. Enter local folder paths in this
panel; the Browse picker is currently Linux-only. No extra installation is needed.
Linked folders and network shares are
not supported yet. Other connected programs and browser tools may still be
unavailable in that build; sharing a folder does not enable them.

## Skills

New desktop installations include **topic-research**, enabled and ready to use.
It guides web research, comparisons and checking claims against sources. Kejtu
can choose it for a relevant request, or you can select it in the message box.
Inspect or disable it in **Shelf → Installed → Skills**; reopening preserves your choice.
It uses existing permissions and does not schedule work or save notes by itself.

A skill is a reusable procedure plus reference files. It adds instructions,
not permissions or a new executable tool. **Shelf → Installed → Skills → Add skill**
imports a local SKILL.md folder, pasted instructions or a public GitHub folder.
For GitHub, you can paste a repository link if it contains one skill. When it
contains several, open the skill folder on GitHub and paste that folder's URL.
Use **Import skill** to add it. Folder imports include supporting files and nested
folders; pasted text includes only SKILL.md. The name declared inside SKILL.md
identifies the skill even when the source folder has a different name.
Imported and updated skills start disabled; review them and enable deliberately.
Kejtu can discover enabled skills. To select one yourself, type **/** or **/skill**
in the message box, then type part of its name. Use the arrow keys and Enter,
or click a single-line entry. The selection appears beside the message box;
clear it to return to Automatic. Escape closes the picker without sending.

The built-in **kejtu-app-help** skill reads this guide without installing anything.
Shelf includes three packages. **Projects** helps organize work and find linked
files and conversations. **Learning & study** helps with tutoring, study
planning and course research. **Development** helps Kejtu work on code using
the terminal connection when you have enabled it and granted its tools. The
packages update with Kejtu. In Shelf you can disable a package or individual
skills and keep your projects, conversations and files. Tool permissions still apply.
Installed scripts are preserved as files; the skill library does not execute them.
Complete main instructions must fit the current context or refuse explicitly.

## Shelf

**Shelf** in the main sidebar opens **Browse**, the catalogue of optional packages.
Select a package to see its source and parts.

Choose **Installed** to see your packages alongside standalone skills and MCP
connections, including ones switched off. Each package appears once.
Use **Find** to search by name or description. Searching for a package part also
finds its package. Open a package to see its parts or choose the **Skills** or
**Connections** filter to go straight to an individual item. The shortcuts in
**Settings → Skills & connections** open those same filters. A local import appears
on your own Shelf.
Names on the Shelf read as words: a skill called `lab-writing` shows as
**Lab writing**, and a package's own connection shows the package's title. Each
item's page lists its exact **Name**, which the **/** picker also uses.
On the **Skills** and **Connections** filters the button at the top reads
**Add skill** or **Add connection**.
**Install from** also accepts a local package folder.
Choose the whole plugin folder, not its hidden metadata folder. Kejtu reads
Agent Plugins, Claude and Codex plugins, unpacked MCPB bundles and single skill
folders. **Preview package** shows its skills, connections and unsupported parts.

You can find these optional packages by name in **Browse**.

- **Context7** looks up current documentation for programming libraries. Public lookups use Context7’s online service and work without signing in.
- **Anti-AI Writing** helps you review or rewrite formulaic passages in English and Spanish.
- **Obsidian Markdown** helps you write notes with wikilinks, properties and callouts. To save a note into your vault, grant write access to its folder.
- **JSON Canvas** helps you create a `.canvas` diagram of connected notes or ideas. Open the file in Obsidian or another JSON Canvas viewer.

**Install & enable** sets up the included skills and tools. Account sign-in, when
needed, stays in Shelf. Open an installed package to see its individual skills
and connections. **Advanced** starts expanded and lets you collapse or reopen that list.
Product-specific app registrations, agents and hooks are not imported. Skills
keep their original instructions and files; instructions for tools Kejtu does not
have cannot grant that access. A package cannot replace an existing connection
with saved credentials. The preview warns about existing package, skill or
connection names, and connections using the same remote service host.
A name conflict requires removal first; switching it off does not free the name.
A shared host only suggests similar tools. Review the existing connection before
enabling the new one, and disable or remove it if you are replacing it. New parts
start off; the warning does not decide that the integrations are interchangeable.
Open the existing item to make that choice; no login or ownership transfers.
Use the installed package's **Look for an update** action when you intend an update.
Updating or removing a package uses its recorded parts
and preserves connections independently replaced by the user.

After restoring a backup, connections stay off. **Shelf** lists programs to
reinstall and connections needing a fresh sign-in or keys. Account passwords,
keys and login tokens are not recovered from the backup. Open **Connections** to
sign in or enter keys yourself, then enable the tools you want.

## Connections

**Shelf → Installed → Connections** shows each connection as a card with its
state. Open one to switch it on or off. When Browser, Spreadsheets, PDFs or
Documents is missing, its card says **Not set up**; **Set up** checks that tool
and enables its full tool selection. It keeps existing disabled or limited choices. On Linux the setup
uses the folders already shared in Files, with their saved read/write access;
if no folder is shared, choose **Choose folders in Files**, then use
**Set up working folder** there or choose another folder. It does not download a
missing runtime or share any additional folder automatically.

Select a connection and choose **Use all available tools** to enable its complete
checked toolset. **Advanced** contains individual tool choices, repeat-call
controls and technical configuration. Switching a connection off and on keeps
its saved selection. Connection checks discover capabilities; they do not prove
that a particular document operation has succeeded.

### Terminal Use and Quick Terminal on Linux

Open **Shelf → Installed → Connections**, open the terminal you want and turn it
on. Both are included and start off. On an existing installation, a missing
terminal shows as **Not set up**; **Set up** on its card restores the entry
after updating Kejtu.

**Quick Terminal** runs one Bash command per call and reports its output and exit
code. It uses Kejtu’s included Python. **Terminal Use** also supports interactive
terminal sessions and reading long command output in pages. It uses the system
Node.js at `/usr/bin/node`. If Kejtu reports an older version, update the system
Node.js package to **22.12 or newer**, then turn on Terminal Use again. Quick
Terminal works while you keep your current Node.js version.

Each terminal starts in its own sandbox with network access off. To work on your
files, open **Settings → Privacy & access → Files**, choose the terminal, and save
your folder choices. If you shared a working folder with it, commands start there.

### Windows test-build connections

Availability depends on the installed test build. A missing runtime cannot be
fixed by repeatedly changing settings. Newer prepared builds include a separate
public-page reader and interactive browser; neither uses your personal logins.

The included document tools are GridCraft (spreadsheets), PdfCraft (PDFs) and
WordCraft (documents). On Windows these trusted apps have your Windows account's
file and network access. Folder and read-only choices
under Files govern Kejtu's built-in Files tools, not those document apps. Disable
a document connection to stop Kejtu using it. Additional imported connections may
still be unavailable in the Windows test build. Explain the actual limitation
plainly; do not claim that a configured connection has passed a real operation.
