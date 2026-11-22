# Settings and troubleshooting

## Where to look

Open **Settings** from the gear at the bottom of the sidebar. Its **Close** button
returns to your workspace; Escape also closes it when no save or recovery is pending.

| Settings group | Panel | Purpose |
| --- | --- | --- |
| Everyday | Appearance | Theme, spacing, profile picture, chat thumbnails and activity animation |
| Everyday | Models | Provider, endpoint, model, credentials, context, thinking and reply allowance |
| Everyday | Web | Search provider, web access and search options/keys |
| Skills & connections | Tools and time | Per-message operation and time allowances |
| Skills & connections | Skills | Import, review, enable and inspect procedures |
| Skills & connections | Connections | MCP runtimes, allowed tools and network/folder access |
| Planning | Workspace | Projects, conversational workspace access and calendars |
| Planning | Background work | Notebook tidy, reflection, interests and advanced jobs |
| This conversation | Notes / Conversation | Notebook, history and conversation controls |
| Privacy & access | Permissions & recall | People, reminders, personal tasks and preference recall |
| Privacy & access | Files | Working folder, shared folders and read/write grants |
| Help | Guide | Getting started, capabilities and help topics |
| Help | Diagnostics / turn inspection | Profile, context and execution evidence |

The **Help** card includes the **Guide**, followed by Diagnostics and turn inspection.
In narrow windows, choose **Guide** under **Help** in **Settings section**.

**Research** and **Tasks** are in the main sidebar. Open them directly to review
research findings or manage personal commitments.

Some panels appear only with a selected conversation or an available runtime.
Settings may refuse navigation while a save or recovery is pending, so a draft
or uncertain operation cannot disappear behind another panel.

In **Appearance → Conversation**, **Show your thumbnail** and **Show Kejtu’s
thumbnail** control the pictures beside chat messages independently. Hiding your
thumbnail keeps the saved picture available to show again. **Animate Kejtu**
switches all of Kejtu’s animations on or off: Kejtu gathers its thoughts while
preparing, then thinks, searches, reads or writes along with the reply, works at
a laptop while using tools and blinks once when an answer is finished. A long
reply can finish with a hop. The same switch controls the daily hello, dozing
while away and spinning on a double click. A single click does not animate Kejtu.
When animation or Kejtu’s thumbnail is off, three dots
indicate ongoing work. These local choices apply immediately and
are remembered across restarts. The computer’s reduced-motion preference stops
the animation. Turning off a picture or animation does not stop a reply.

## Following interests

**Background work → Interests** starts off. Turning it on permits occasional
public-information checks based on accessible “about you” notes with verifiable
supporting words from you. A question by itself is not an established interest.
The list shows the supporting quote, last check and next eligible check.
**Not now** keeps that topic quiet for a week; **Stop following** stops it.
You can also use those words directly after an aside. Stopped topics are remembered
after their supporting note is deleted, so deleting a note does not start following
that topic again. Saved answers retain their attribution records.

Checks run only while Kejtu is open and idle, with Web enabled. The daily allowance
is shared across topics, including failed and interrupted attempts. Each check
permits up to two model requests, one search and two page reads within two minutes.
These are work limits, not a guaranteed price. The configured model receives the
supporting notes and original words; search queries contain a public topic rather
than those personal quotes.

Kejtu may add a brief sourced aside to a casual reply. It never starts a conversation
for this feature. There is no daily target: silence is normal, including during
focused or sensitive conversation. Findings expire; “mentioned” requires a completed
answer with a server-checked reference to the finding admitted to that turn.
Interrupted delivery is kept uncertain and is not counted as a completed mention.

## Connect Anthropic

In **Everyday → Models → Anthropic**, save an API key created for a single
workspace in the [Claude Console](https://platform.claude.com/settings/keys).
Use **Check connection**, choose a supported Claude model, save the profile and
select **Use for new conversations**. Existing conversations keep their model.
The key stays in your system keyring; never paste it into a conversation.

This uses Anthropic API billing, separately from a Claude subscription. Signing
in with a Claude subscription is not supported. The default is Opus 5.5 at Medium
effort; Sonnet 5.5, Haiku 5.5 and Fable 5.2 are also available. The reply allowance
includes thinking. Text streaming, image input and Kejtu's permitted tools use the
same conversation controls as other providers.

## A reply will not start

Check Models for the active connection, credentials, model and compatible limits.
An endpoint may be unreachable, the key missing, or the configured input/reply
reserve too large. Save the intended profile. Do not paste keys into chat.
If a pending-operation notice is shown, use its recovery control first; do not
repeatedly submit the same request.

## A tool is missing or fails

Check the specific feature's panel. Distinguish permission off, runtime not
installed, missing credentials, an unavailable service and a partial result.
A Files grant does not install its server; Web does not create a browser MCP;
a skill does not grant its requested tools. Tool activity beside the message
shows actual calls and results. A timeout is not evidence that an external
write failed safely, so uncertain actions should not be repeated automatically.
In Connections, **Needs attention** means that connection's program is missing:
only that connection stops, and its saved tool choices apply again once the
program is back. Reinstall it or remove the connection.

## Kejtu cannot recall something

Check whether the original conversation or note exists, its current access and
whether it was excluded. Ask Kejtu to search and read the relevant original.
A failed search is not proof of absence, and stored history is not unlimited
model context. A saved note may be mistaken or out of date; inspect its sources
and revisions rather than treating it as ground truth.

## Deletion does not complete

Use **Message actions → Delete exchange** and read the confirmation. Finish or
stop active work first if the app reports it is busy. A maintenance-limit or
storage error means deletion was not confirmed; repeating it indefinitely is not
a fix. Record the exact error and whether it was an exchange, notebook note or
research assignment. Those are different removal paths. After an uncertain
response, use the pending-operation recovery control before a new intent.

## Context or work allowance is exhausted

The saved model profile owns context capacity and the reply reserve. **Tools and
time** bounds execution. Increasing one does not fix the other, nor overcome the
provider's actual limits. Required instructions or source pages must fit or be
reported unavailable; the app should not quietly truncate them. Try a narrower
task, or a supported profile with enough room. This preview still has known
small-window configuration cases under investigation.

Kejtu can explain this manual and the tool results it receives. It cannot see all
local Settings, hidden errors or arbitrary source files merely because it is the
assistant inside the app. If it cannot verify a setting, it should name the
specific control to check rather than invent a diagnosis.


## A file is missing from the conversation sidebar

Check the module name beside the message field. Expand **Resources** under
**Current module** and choose **Refresh folder** after adding a file. If the
folder is missing or wrong, open **Projects**, choose the project, use the
module’s edit button, check its **Resources folder** and choose **Save module**.
Click the file in the conversation sidebar to open its tab beside chat.

## Shelf

Open **Shelf** in the main sidebar. **Installed** lists your packages, standalone
skills and MCP connections. The **Skills** and **Connections** shortcuts in Settings
open the matching Shelf filter, including items that are turned off. **Browse** offers a small
selection from original public repositories; use the search or categories to find
one. Each entry shows its publisher, what it adds, account requirements and size.
Choose a package by name to see its original source, licence and parts before installation.
You can still import a package from a folder.

The list works offline. Installing needs Web access and downloads the exact
upstream files shown. **Install & enable** sets up the included skills and all
available tools. If an account is needed, sign in within Shelf; setup continues
after sign-in. On an installed package, **Advanced** initially shows its individual parts.
A free plugin can still require a service account or subscription. Installing another app's package never copies
that app's login. Host-specific parts that Kejtu cannot use are listed in preview.

**Installed** holds the existing disable, update, repair, backup and removal
controls. An offered Shelf update is explicit and keeps the existing package
ownership rules. Similar packages and hand-added connections remain separate;
follow the overlap notice instead of replacing them automatically.
