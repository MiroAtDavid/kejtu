# Start with one useful conversation

Kejtu is a personal AI workspace: conversations, inspectable memory, projects,
documents and tools you choose to enable. A model supplies its replies; no model
or paid account is bundled. This guide is available offline from **Settings → Help**, even
before a model is connected.

## 1. Connect a model

Open **Settings → Everyday → Models**. Choose a supported connection, enter your
own API key there if required, check the connection, and save/activate the profile.
The app supports Ollama, DeepSeek, Kimi, OpenAI API and ChatGPT plan connections; available models
and thinking choices are shown in the panel. Ollama can use a local server or a
server elsewhere: its endpoint determines where inference runs.

The source-preview first-run helper currently seeds DeepSeek with a 264,000-token
context, a 16,384-token reply reserve and medium thinking. Use limits supported
by your chosen model. A larger number is not a guarantee the provider accepts it.
No key is supplied. Missing credentials or an incompatible context can prevent
the first message; Help itself still works.

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

## A short guided introduction

On a fresh installation, choose **Get started with Kejtu**. It opens a conversation
and prepares a first message for you to review and send. Kejtu asks what you want
help with and guides one useful task. A connected model is needed for its replies.

Choose **I’m ready** when you are comfortable, or **Skip introduction** at any time
between replies. The introduction and its temporary instructions disappear, even
after restarting. Your conversation stays. Setup instructions are not saved as a
notebook note, and the introduction does not grant access to files or other tools.
You can always return to **Settings → Help → Guide**.

## 2. Try a small request

Open **Conversations**, create a conversation if needed, and send something like:

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
The repository README, FILES.md and MCP.md describe those developer setup steps.

## 4. Find your way around

**Home** shows today's Kejtu schedule, tasks and recent conversations. The sidebar
opens conversations and Projects; narrow windows use the sidebar toggle.
**Settings** contains connections, permissions and managers. **Widgets** opens
the optional widget area. Research results are available from Home and Settings.
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
