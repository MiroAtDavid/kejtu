# Settings and troubleshooting

## Where to look

| Settings group | Panel | Purpose |
| --- | --- | --- |
| Everyday | Appearance | Theme, spacing, profile picture, chat thumbnails and activity animation |
| Everyday | Models | Provider, endpoint, model, credentials, context, thinking and reply allowance |
| Everyday | Web | Search provider, web access and search options/keys |
| Skills & connections | Tools and time | Per-message operation and time allowances |
| Skills & connections | Skills | Import, review, enable and inspect procedures |
| Skills & connections | Connections | MCP runtimes, allowed tools and network/folder access |
| Planning | Workspace | Projects, conversational permissions, calendar and media |
| Planning | Background work | Notebook tidy, reflection and advanced jobs |
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
thumbnail keeps the saved picture available to show again. **Animate Kejtu while
working** switches the activity animation: Kejtu gathers its thoughts while
preparing, then thinks, searches, reads or writes along with the reply, works at
a laptop while using tools and blinks once when an answer is finished. When animation or Kejtu’s thumbnail is off, three dots
indicate ongoing work. These local choices apply immediately and
are remembered across restarts. The computer’s reduced-motion preference stops
the animation. Turning off a picture or animation does not stop a reply.

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
