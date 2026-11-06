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
**Settings → Skills & connections → Connections** manages it and other MCP servers. MCP is a way
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
requires **Save changes**. Folder edits are kept when switching access off.
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
Inspect or disable it in **Settings → Skills**; reopening preserves your choice.
It uses existing permissions and does not schedule work or save notes by itself.

A skill is a reusable procedure plus reference files. It adds instructions,
not permissions or a new executable tool. **Settings → Skills & connections → Skills**
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
Project organization, planning, tutoring and course-research procedures are also
built in when Projects is enabled. They still need actual source/tool access.
Installed scripts are preserved as files; the skill library does not execute them.
Complete main instructions must fit the current context or refuse explicitly.

Earlier installations can import the shipped
`src/kejtu_next/starter_skills/topic-research` folder in Skills, then enable it.
An additional demo MCP is not yet included. Browser runtimes are installed by the
first-run helper. An earlier installation can install them with `./kejtu setup-web`
from the Kejtu folder, then select **Connections → Set up included tools**.

## Connections

**Settings → Skills & connections → Connections** shows simple On/Off controls
for Browser, Spreadsheets, PDFs and Documents. **Set up included tools** finds
missing installed tools, checks their connection and enables their full tool
selection. It keeps existing disabled or limited choices. On Linux the setup
uses the folders already shared in Files, with their saved read/write access;
if no folder is shared, choose **Choose folders in Files**, then use
**Set up working folder** there or choose another folder. It does not download a
missing runtime or share any additional folder automatically.

Select a connection and choose **Use all available tools** to enable its complete
checked toolset. **Advanced** contains individual tool choices, repeat-call
controls and technical configuration. Switching a connection off and on keeps
its saved selection. Connection checks discover capabilities; they do not prove
that a particular document operation has succeeded.

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
