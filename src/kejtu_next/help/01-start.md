# Start with one useful conversation

Kejtu is a personal AI workspace: conversations, inspectable memory, projects,
documents and tools you choose to enable. A model supplies its replies; no model
or paid account is bundled. This guide is available offline from **Settings → Help**, even
before a model is connected.

## 1. Connect a model

Open **Settings → Everyday → Models**. Choose a supported connection, enter your
own API key there if required, check the connection, and save/activate the profile.
Choose from Ollama, Anthropic, DeepSeek, Kimi, OpenAI API and ChatGPT plan connections.
The model panel shows the available models and thinking settings. With Ollama,
the endpoint you select determines where the model runs. Use the context and
reply limits supported by the model. You can read Help before connecting one.

### Use your ChatGPT subscription

In Models, select **ChatGPT plan → Continue with ChatGPT** and approve the
connection in your browser. No Codex installation or API key is needed.
The model list loads after sign-in. Use **Load models** or **Refresh models** beside
the picker to load it again. Choose a model, save the profile, and select
**Use for new conversations**. Existing conversations retain their original
provider. Kejtu continues to manage memory, skills and tools itself.

A connection check lists models without generating an answer. Send a short new
conversation to verify inference. Manage account/app limits through **Manage
ChatGPT usage**. Requests never fall back to API billing. Sign-out and additional
accounts are available in the same panel.

This connection cannot impose a server-side generation cap. The reply allowance
is checked on completion; excessive output stops before its tool calls run.
Requests can still consume more than that allowance. Time and response-size
bounds remain enforced. Only the models supported by this Kejtu version can be
selected. ChatGPT conversations and ChatGPT memory are not imported.

## The welcome window

On a fresh installation, a small welcome window opens once, in five short steps:

- **Choose a model**: **Open Models** opens **Settings → Models**, where you choose
  a model and enter its key. The window shows whether a key is saved; it never
  asks for one, and Kejtu never asks for keys in a chat. **Later** moves on.
- **A folder for Kejtu?**: **Create folder** makes a Kejtu folder in your home
  folder, or in the place you choose, with read & write access. It is the same as
  **Set up working folder** in **Settings → Files**. Other folders stay private
  until you share them there. **Not now** moves on without any file access.
- **Tools for Kejtu?**: Browser, PDFs, Spreadsheets and Documents are ticked;
  on Linux, Quick Terminal is offered unticked. **Turn on** sets up and switches
  on the ticked tools, the same as **Set up** on their cards in **Shelf →
  Installed**. The document tools work in your Kejtu folder, or in the folders you
  share in **Settings → Files**. Each tool keeps its own switch in the Shelf.
  **Not now** moves on without turning anything on.
- **Help**: **Open Help** opens this guide; **Done** closes the window.

**Skip setup**, the close button or Escape ends the window on any step. It does not
come back after Done or Skip, even after restarting; everything it offers stays in
Settings and the Shelf. If you close Kejtu halfway, it opens again on the next start, with the
steps already done marked.

## A short guided introduction

On the welcome window’s last step, **Or let Kejtu show you around in a
conversation** opens a conversation and prepares a first message for you to review
and send. It appears once a model is ready. Kejtu asks what you want help with and
guides one useful task.

Choose **I’m ready** above that conversation when you are comfortable. The
introduction and its temporary instructions disappear, even after restarting. Your
conversation stays. Setup instructions are not saved as a notebook note, and the
introduction does not grant access to files or other tools. You can always return
to **Settings → Help → Guide**.

## 2. Try a small request

Start a conversation with **+** at the top left of the window (or **Ctrl+N**), and
send something like:

> Help me break this afternoon's work into three manageable steps.

A normal reply checks the conversation connection. It does not prove that search,
file access, reminders or document editing are configured. You can also ask:

> Read your Kejtu help guide and explain what I can do here. Start with the
> settings relevant to working with my documents.

Kejtu has a built-in help skill in tool-enabled conversations. It can read this
version's guide without web access or access to your folders. If the selected
model cannot use tools, read Help directly. Help's **Ask Kejtu** action prepares
a draft for you to send; it does not make a model request by itself.

## 3. Enable what your task needs

- For current information: new installations start with Tavily search and Web
  access enabled. **Everyday → Web** changes that choice. The standard
  setup also enables a temporary browser under **Connections** and includes the
  **topic-research** skill for source research and comparisons.
- For your documents: configure **Privacy & access → Files**, select only the
  folders you want to share, and choose read or write access.
- For projects: open **Planning → Workspace**, show Projects and review the
  separate conversational read/change permissions.
- For People, reminders or personal tasks: review **Privacy & access →
  Permissions & recall**. Local controls and model permissions are distinct.

Optional runtime installation is still part of this source preview's setup.
An enabled checkbox cannot install a missing browser, MCP worker or file server.
Open **Shelf → Installed → Connections** to see your connected tools.

## 4. Find your way around

**Home** is in the sidebar above **Conversations**. The sidebar also opens
Calendar, Projects, Research, Shelf and Tasks. In narrow windows, use the sidebar toggle.

- In **Projects**, open a project to see its modules and open tasks, or switch to **Conversations** for its chats.
- In **Tasks**, open a saved task to read its description in Preview. **New task** opens in Write.
- In **Research**, choose a saved search for findings, criteria and reports. **New research** starts a chat draft.
- Open **Shelf** to browse optional packages. Choose **Installed** to manage your packages, skills and connections.

**Settings** is at the bottom of the sidebar, beside a gear icon. It opens a dialog
for connections, permissions and preferences. Choose **Close** to return to the
same conversation or project, with your scroll position and draft retained.
To work on a file, open **Projects**, choose a project, expand its module and
choose **Conversation**. Or select the module beside the message field of an
existing chat. Open **Resources** in the chat sidebar and click a file to open
it on the right. You can edit Markdown there or mark a PDF paragraph and ask
about it in the same conversation.
**Settings → Help** can be reopened at any time. There is no setup wizard that silently
shares folders, imports personal data or enables scheduled work.

## Images and skills in a message

Paste or drag PNG/JPEG images into the message box. There is no attachment button.
Each image must be at most 1.4 MB and 4096 × 4096 pixels, with at most 32 per message.
The **combined** images and text must also fit this conversation's saved model
profile. The composer shows the approximate total image allowance for the current
text and rejects a batch that will not fit, keeping the existing draft. A smaller
model profile can allow much less than 1.4 MB in total. Use smaller images or
separate messages. Images are preserved exactly; Kejtu does not silently compress
them. The selected model must support image input.

Type **/** or **/skill** to choose an enabled skill in the compact picker. Select a
name with the arrow keys and Enter or with a click. Selecting does not send the
message. Clear the selected name to let Kejtu choose automatically.
