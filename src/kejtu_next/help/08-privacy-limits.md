# Privacy and practical limits

Kejtu's archive is local. Model requests go to the configured endpoint. A local
Ollama endpoint can keep inference on your machine; a remote endpoint receives
the context sent in each request, including relevant chat, memory and tool data.
Web/search and connected tools contact their own services even when the model is
local. Local storage is not a promise that every action is offline.

After the first completed exchange, Kejtu may make one additional model request
to name the conversation while the chat is idle. It uses short excerpts of that
exchange and the conversation’s saved model connection, so a remote provider
receives those excerpts and the request uses its allowance. Saved titles are
reused; failed attempts are not automatically retried.

Provider credentials belong in Settings and use the supported credential store.
ChatGPT sign-in tokens are kept separately in owner-only files inside the private
application state, and refreshed when needed. Treat state backups as private too.
They are never stored in browser storage or the source release.
Do not put secrets in prompts, profiles committed to Git or shared examples.
Folder sharing, connection/tool permissions and source access limit what tools
can read or change. Sharing a parent folder does not expose Kejtu’s private
archive, launch settings, backups or logs. Project Resources applies the same
private-state boundary. Existing read-only program/dependency mounts may be listed
separately in Connections. Review the actual saved choices.

## What is not promised

- Perfect recall, correctness, document fidelity or semantic understanding.
- Automatic access to all files, accounts, browsers, contacts or email.
- A bundled model, paid API key or fully provisioned optional runtime.
- A permanent background service or desktop notification alarms.
- General document-vault/vector-database ingestion from the older app.
- Arbitrary terminal/script execution merely because a skill contains code.
- Operating-system-wide control, automatic email sending or account login
  without an actual authorized connection that supplies those actions.
- Complete erasure of quotations, exports, backups or provider-held data when
  one exchange is deleted.

This is an experimental Linux source preview for people comfortable with setup.
Mac and Windows installation have not been verified, and desktop installer
bundling is disabled. Some optional tools depend on Linux services, Bubblewrap,
mpv or external providers. The interface and first-run installation still have
known unfinished work; the README and release status describe it.

The published LongMemEval development result concerns a specific history-only
test. It is not a measure of every feature, a reliability guarantee or evidence
that a newly configured local model behaves identically.

## Keep results inspectable

Review meaningful changes and source links. Back up your archive and shared
working files; they are separate stores. The source distribution must not contain
personal archives, credentials or private test data. This manual is part of the
application source and contains no personal installation state.
