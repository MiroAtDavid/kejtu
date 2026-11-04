use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs;
#[cfg(any(unix, test))]
use std::io::{BufRead, BufReader};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
#[cfg(unix)]
use std::sync::mpsc;
#[cfg(any(unix, test))]
use std::thread;
use std::time::Duration;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::{backend_process::OwnedChild, private_file, transport, wire};

static REQUEST_COUNTER: AtomicU64 = AtomicU64::new(1);

/// Window title for the development runtime. Owned by the frontend route
/// (`routes/+page.svelte`); the renderer receipt must echo it exactly.
pub const DEVELOPMENT_WINDOW_TITLE: &str = "Kejtu Next — DEVELOPMENT";

/// Renderer-readiness marker published by the frontend on its root element.
///
/// This is the startup contract, and it is deliberately a data attribute rather
/// than a visible heading. It previously scraped `header strong`, which tied
/// startup to layout text: the fe003 visual port replaced that header and the
/// native command rejected every receipt, rendering the app "Unavailable".
///
/// Both sides must change together. The frontend test asserts the value the
/// client sends; this file's `rendered_marker_matches_frontend_contract` test
/// asserts this constant; and `recordRendered` reads it from the DOM rather than
/// from presentational text, so restyling can no longer break startup silently.
///
/// Keep in sync with `NATIVE_READINESS_MARKER` in `src/lib/api/client.ts`.
pub const RENDERED_MARKER: &str = "kejtu-native-rendered";

/// Validate one `native_rendered` receipt.
///
/// Kept as a free function so the contract can be tested without constructing a
/// backend: a mismatch between the two sides must fail a test, not just a launch.
fn validate_rendered_evidence(evidence: &Value) -> Result<(&str, &str, u64, u64, &str), String> {
    let title = evidence.get("title").and_then(Value::as_str);
    let marker = evidence.get("marker").and_then(Value::as_str);
    let width = evidence.get("width").and_then(Value::as_u64);
    let height = evidence.get("height").and_then(Value::as_u64);
    let phase = evidence.get("phase").and_then(Value::as_str);
    if title != Some(DEVELOPMENT_WINDOW_TITLE)
        || marker != Some(RENDERED_MARKER)
        || !width.is_some_and(|value| (320..=8192).contains(&value))
        || !height.is_some_and(|value| (240..=8192).contains(&value))
        || !phase.is_some_and(|value| {
            matches!(
                value,
                "ready" | "1" | "reopen" | "delay" | "note_delay" | "note_reopen"
            )
        })
    {
        return Err("rendered development evidence was invalid".into());
    }
    Ok((
        title.unwrap(),
        marker.unwrap(),
        width.unwrap(),
        height.unwrap(),
        phase.unwrap(),
    ))
}

#[derive(Deserialize)]
struct Ready {
    kind: String,
    port: u16,
    session_token: String,
    process_instance_id: String,
    state_id: String,
}

#[derive(Deserialize)]
struct StartupFailure {
    kind: String,
    code: String,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct PendingIntent {
    state_id: String,
    kind: String,
    mutation_key: String,
    target_id: Option<String>,
    predecessor_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    note_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    exchange_id: Option<String>,
    intent_sha256: String,
}

pub struct Backend {
    child: OwnedChild,
    port: u16,
    token: String,
    state_id: String,
    state_dir: PathBuf,
    creation_intent: String,
    submit_ceiling: usize,
}

#[derive(Clone, Copy)]
enum NoteSelection {
    Off,
    Synthetic,
    Write,
}

impl NoteSelection {
    fn enabled(self) -> bool {
        !matches!(self, Self::Off)
    }
}
fn pin_path(state: &Path) -> PathBuf {
    state.with_extension("identity.json")
}

fn write_private(path: &Path, raw: &[u8]) -> Result<(), String> {
    private_file::write(path, raw).map_err(|error| error.to_string())
}

fn backend_python(candidate: &Path) -> PathBuf {
    candidate.join(if cfg!(windows) {
        ".venv/Scripts/python.exe"
    } else {
        ".venv/bin/python"
    })
}

// Desktop startup never waits on /dev/tty. The backend restores its keyring
// credential after readiness, and Settings owns first entry/replacement.
fn settings_key(_: &Path, _: &Path, _: &str) -> Result<Vec<u8>, String> {
    Ok(Vec::new())
}

fn valid_key_bytes(key: &[u8]) -> bool {
    !key.is_empty() && key.len() <= 4096 && key.iter().all(|byte| (33..=126).contains(byte))
}

impl Backend {
    #[cfg(windows)]
    pub(crate) fn exit_owner(&self) -> std::sync::Arc<crate::backend_process::ExitOwner> {
        self.child.exit_owner()
    }

    pub async fn start() -> Result<Self, String> {
        if let Some(package) = crate::package_startup::installed()? {
            return Self::start_selected_with_standing(
                &package.root,
                package.state,
                package.initialize,
                Some(&package.profile),
                NoteSelection::Write,
                Some(&package.standing),
                settings_key,
            )
            .await;
        }
        let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let candidate = manifest
            .parent()
            .and_then(Path::parent)
            .ok_or("candidate path unavailable")?;
        let state_dir = candidate.join(".state/runtime");
        let initialize = std::env::var("KEJTU_R013_INITIALIZE").as_deref() == Ok("1");
        let profile = std::env::var_os("KEJTU_NATIVE_PROFILE").map(PathBuf::from);
        let standing = std::env::var_os("KEJTU_NATIVE_STANDING_PROFILE").map(PathBuf::from);
        let write = std::env::var("KEJTU_NATIVE_NOTES_WRITE").as_deref() == Ok("1");
        let synthetic = std::env::var("KEJTU_R013_SYNTHETIC_NOTES").as_deref() == Ok("1");
        let notes = match (write, synthetic) {
            (false, false) => NoteSelection::Off,
            (true, false) => NoteSelection::Write,
            (false, true) => NoteSelection::Synthetic,
            (true, true) => return Err("conflicting native note selection".into()),
        };
        Self::start_selected_with_standing(
            candidate,
            state_dir,
            initialize,
            profile.as_deref(),
            notes,
            standing.as_deref(),
            settings_key,
        )
        .await
    }







    async fn start_selected_with_standing(
        candidate: &Path,
        state_dir: PathBuf,
        initialize: bool,
        profile_path: Option<&Path>,
        notes: NoteSelection,
        standing_path: Option<&Path>,
        prompt: fn(&Path, &Path, &str) -> Result<Vec<u8>, String>,
    ) -> Result<Self, String> {
        // This is a new-conversation grant, not a profile setting or global
        // revocation. Reject mismatched modes before key entry or store IO.
        if matches!(notes, NoteSelection::Write) && profile_path.is_none()
            || matches!(notes, NoteSelection::Synthetic) && profile_path.is_some()
        {
            return Err("native note selection does not match provider mode".into());
        }
        // Explicit private text selection, pinned before key entry/store IO.
        // Only its path/digest cross argv; the contents stay in Python's saved
        // conversation configuration, not WebView settings or startup output.
        let standing_digest = if let Some(path) = standing_path {
            if !path.is_absolute()
                || !fs::symlink_metadata(path)
                    .map_err(|_| "standing profile unavailable".to_string())?
                    .file_type()
                    .is_file()
            {
                return Err("standing profile requires an absolute regular file".into());
            }
            let mut raw = Vec::new();
            fs::File::open(path)
                .and_then(|file| file.take(16385).read_to_end(&mut raw))
                .map_err(|_| "standing profile unavailable".to_string())?;
            if raw.len() > 16384 || std::str::from_utf8(&raw).is_err() {
                return Err("standing profile exceeds the UTF-8 input bound".into());
            }
            Some(sha256_hex(&raw))
        } else {
            None
        };
        // Selection comes from the operator launcher, never from an invoke
        // payload. Pin exact bytes before spawn; Python verifies before store IO.
        let selected = if let Some(path) = profile_path {
            if !path.is_absolute() {
                return Err("native profile path must be absolute".into());
            }
            if !fs::symlink_metadata(path)
                .map_err(|_| "native profile unavailable".to_string())?
                .file_type()
                .is_file()
            {
                return Err("native profile must be a regular file".into());
            }
            let mut raw = Vec::new();
            fs::File::open(path)
                .and_then(|file| file.take(16385).read_to_end(&mut raw))
                .map_err(|_| "native profile unavailable".to_string())?;
            if raw.len() > 16384 {
                return Err("native profile exceeds the input bound".into());
            }
            let value: Value =
                serde_json::from_slice(&raw).map_err(|_| "native profile malformed".to_string())?;
            if !value.is_object() {
                return Err("native profile must be an object".into());
            }
            Some((sha256_hex(&raw), value))
        } else {
            None
        };
        let pin = pin_path(&state_dir);
        let expected_state = if initialize {
            if pin.exists() {
                return Err("state identity already initialized".into());
            }
            None
        } else {
            let saved = fs::read_to_string(&pin).map_err(|_| {
                "expected state identity missing; initialization is explicit".to_string()
            })?;
            if saved.len() != 32
                || !saved
                    .bytes()
                    .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
            {
                return Err("expected state identity malformed".into());
            }
            Some(saved)
        };
        let python = backend_python(candidate);
        let provider_mode = match selected
            .as_ref()
            .map(|(_, profile)| profile.get("provider"))
        {
            None => "development_fake",
            Some(None) => "ollama", // Existing Profile default.
            Some(Some(value)) => match value.as_str() {
                Some(name) if wire::PROVIDERS.iter().any(|(id, _)| *id == name) => name,
                _ => return Err("native provider unsupported".into()),
            },
        };
        // The default desktop path supplies no key. A private pipe remains
        // available to explicit offline/native tests, never a console prompt.
        let mut key = if wire::PROVIDERS
            .iter()
            .any(|(id, uses_key)| *id == provider_mode && *uses_key)
        {
            let (digest, _) = selected.as_ref().ok_or("native profile missing")?;
            let value = prompt(
                candidate,
                profile_path.ok_or("native profile missing")?,
                digest,
            )?;
            if value.is_empty() {
                None
            } else if valid_key_bytes(&value) {
                Some(value)
            } else {
                return Err("Provider key entry refused".into());
            }
        } else {
            None
        };
        fs::create_dir_all(&state_dir)
            .map_err(|_| "development state directory unavailable".to_string())?;
        let mut command = Command::new(python);
        command
            .current_dir(candidate)
            .env("PYTHONPATH", candidate.join("src"))
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .args(["-B", "-m", "kejtu_next", "native-server", "--state-dir"])
            .arg(&state_dir)
            .stdin(if key.is_some() {
                Stdio::piped()
            } else {
                Stdio::null()
            })
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        if initialize {
            command.arg("--initialize");
        } else if let Some(expected) = &expected_state {
            // Selection is verified by the store before SQLite recovery, not
            // only by the post-start readiness/hello checks below.
            command.args(["--expected-state-id", expected]);
        }
        if let (Some(path), Some((digest, _))) = (profile_path, &selected) {
            command
                .arg("--profile")
                .arg(path)
                .args(["--profile-sha256", digest]);
        } else {
            command.arg("--development-fake");
        }
        if let (Some(path), Some(digest)) = (standing_path, standing_digest) {
            command
                .arg("--standing-profile")
                .arg(path)
                .args(["--standing-profile-sha256", &digest]);
        }
        match notes {
            NoteSelection::Off => {}
            NoteSelection::Synthetic => {
                command.arg("--synthetic-notes");
            }
            NoteSelection::Write => {
                command.arg("--notes-write");
            }
        }
        if key.is_some() {
            command.arg("--model-key-stdin");
        }
        let mut child = OwnedChild::spawn(&mut command, key.is_some())
            .map_err(|_| "candidate backend could not start".to_string())?;
        if let Some(mut raw) = key.take() {
            let result = child
                .stdin
                .take()
                .ok_or("Provider startup key pipe unavailable")?
                .write_all(&raw);
            raw.fill(0);
            result.map_err(|_| "Provider startup key handoff failed".to_string())?;
            // Dropping stdin marks EOF for the bounded backend read; it is
            // never left inherited by model workers or a UI process.
        }
        #[cfg(windows)]
        let line = match child.readiness_line(Duration::from_secs(5)) {
            Ok(line) => line,
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err("candidate readiness unavailable".into());
            }
        };
        #[cfg(unix)]
        let line = {
            let stdout = child
                .stdout
                .take()
                .ok_or("candidate readiness pipe unavailable")?;
            let (sender, receiver) = mpsc::channel();
            thread::spawn(move || {
                let mut line = String::new();
                let result = BufReader::new(stdout)
                    .take(4097)
                    .read_line(&mut line)
                    .map(|_| line);
                let _ = sender.send(result);
            });
            let line = match receiver.recv_timeout(Duration::from_secs(5)) {
                Ok(Ok(line)) => line,
                _ => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err("candidate readiness unavailable".into());
                }
            };
            line
        };
        if let Ok(failure) = serde_json::from_str::<StartupFailure>(&line) {
            if failure.kind == "failed" {
                return Err(if failure.code == "state_identity" {
                    "candidate state identity changed or unreadable; explicit operator selection required"
                } else {
                    "candidate startup refused"
                }.into());
            }
        }
        let ready: Ready =
            serde_json::from_str(&line).map_err(|_| "candidate readiness malformed".to_string())?;
        if ready.kind != "ready"
            || ready.session_token.len() != 64
            || ready.process_instance_id.len() != 32
            || ready.state_id.len() != 32
            || ready.port == 0
        {
            let _ = child.kill();
            return Err("candidate readiness incompatible".into());
        }
        if expected_state
            .as_ref()
            .is_some_and(|expected| expected != &ready.state_id)
        {
            return Err(
                "candidate state identity changed; explicit operator selection required".into(),
            );
        }
        let process_id = ready.process_instance_id.clone();
        let mut backend = Self {
            child,
            port: ready.port,
            token: ready.session_token,
            state_id: ready.state_id,
            state_dir,
            creation_intent: String::new(),
            submit_ceiling: wire::CONTROL_REQUEST_BYTES,
        };
        let hello = backend
            .request(json!({"protocol":wire::PROTOCOL,"request_id":request_id(),
            "operation":"hello","arguments":{}}))
            .await?;
        let data = hello.get("data").ok_or("candidate hello missing")?;
        if data.get("schema_sha256").and_then(Value::as_str) != Some(wire::SCHEMA_SHA256)
            || data.get("state_id").and_then(Value::as_str) != Some(backend.state_id.as_str())
            || data["capabilities"]["notes_write"].as_bool() != Some(notes.enabled())
            || (provider_mode == "development_fake"
                && data.get("provider_mode").and_then(Value::as_str) != Some(provider_mode))
            || (provider_mode != "development_fake"
                && !data["provider_mode"]
                    .as_str()
                    .is_some_and(|name| wire::PROVIDERS.iter().any(|(id, _)| *id == name)))
            || data.get("process_instance_id").and_then(Value::as_str) != Some(process_id.as_str())
            || data.get("build_fingerprint").and_then(Value::as_str)
                != Some(env!("R013_BUILD_SHA256"))
            || data.get("store_schema_version").and_then(Value::as_u64) != Some(39)
        {
            let _ = backend.child.kill();
            return Err("candidate identity incompatible".into());
        }
        backend.creation_intent = data["creation_intent_sha256"]
            .as_str()
            .ok_or("creation intent missing")?
            .into();
        let context = data
            .get("ingress_context_limit")
            .unwrap_or(&data["profile"]["context_limit"])
            .as_u64()
            .ok_or("profile context missing")?;
        backend.submit_ceiling = usize::try_from(context)
            .ok()
            .and_then(|v| v.checked_mul(24))
            .and_then(|v| v.checked_add(4096))
            .ok_or("profile ingress bound invalid")?;
        if initialize {
            write_private(&pin, backend.state_id.as_bytes())?;
        }
        Ok(backend)
    }

    pub async fn request(&mut self, request: Value) -> Result<Value, String> {
        if !wire::validate_request(&request) {
            return Err("native request failed generated validation".into());
        }
        if self.creation_intent.is_empty()
            && matches!(
                request["operation"].as_str(),
                Some("conversation.create" | "turn.submit")
            )
        {
            self.refresh_profile().await?;
        }
        let raw =
            serde_json::to_vec(&request).map_err(|_| "native request malformed".to_string())?;
        let operation = request
            .get("operation")
            .and_then(Value::as_str)
            .ok_or("native operation missing")?;
        let ceiling = if operation == "turn.submit" {
            self.submit_ceiling
        } else {
            wire::CONTROL_REQUEST_BYTES
        };
        if raw.len() > ceiling {
            return Err("native request exceeded its byte bound".into());
        }
        let is_mutation = matches!(
            operation,
            "conversation.create" | "turn.submit" | "note.remove" | "exchange.action"
        );
        if is_mutation {
            self.write_pending(operation, &request)?;
        }
        if operation == "models.save" {
            // A timed-out save may have committed. Mark the cached creation
            // identity unknown until a fresh backend receipt confirms it.
            self.creation_intent.clear();
        }
        let result = transport::exchange(self.port, &self.token, &request).await;
        if matches!(operation, "models.save" | "models.read") {
            self.refresh_profile().await?;
        }
        if is_mutation {
            if let Ok(value) = &result {
                if value["kind"] == "result" {
                    let pending = self.pending()?.ok_or("pending mutation record missing")?;
                    self.check_receipt(&pending, &value["data"], false)?;
                    self.clear_pending()?;
                }
                // Even a valid application error can follow a durable commit.
                // Resolve every uncertain mutation by lookup, never by resend.
            }
        }
        result
    }

    async fn refresh_profile(&mut self) -> Result<(), String> {
        let hello = self
            .request_without_marker(json!({"protocol": wire::PROTOCOL,
            "request_id": request_id(), "operation": "hello", "arguments": {}}))
            .await?;
        let data = &hello["data"];
        if data["state_id"].as_str() != Some(self.state_id.as_str()) {
            return Err("model settings state identity changed".into());
        }
        let intent = data["creation_intent_sha256"]
            .as_str()
            .ok_or("creation intent missing")?;
        let context = data
            .get("ingress_context_limit")
            .unwrap_or(&data["profile"]["context_limit"])
            .as_u64()
            .ok_or("profile context missing")?;
        self.submit_ceiling = usize::try_from(context)
            .ok()
            .and_then(|v| v.checked_mul(24))
            .and_then(|v| v.checked_add(4096))
            .ok_or("profile ingress bound invalid")?;
        self.creation_intent = intent.to_string();
        Ok(())
    }

    pub async fn reconcile_pending(&mut self) -> Result<Value, String> {
        let path = self.state_dir.join("pending-intent.json");
        if !path.exists() {
            return Ok(Value::Null);
        }
        let pending = self.pending()?.ok_or("pending intent missing")?;
        let mut arguments = json!({"kind":pending.kind,"mutation_key":pending.mutation_key});
        if pending.kind == "note.remove" {
            arguments["conversation_id"] = json!(pending.target_id);
        }
        let response = self
            .request_without_marker(json!({"protocol":wire::PROTOCOL,"request_id":request_id(),
            "operation":"mutation.lookup","arguments":arguments}))
            .await?;
        if response.get("kind").and_then(Value::as_str) == Some("result") {
            if response["data"]["status"] == "accepted" || response["data"]["status"] == "removed" {
                self.check_receipt(&pending, &response["data"], true)?;
            }
            self.clear_pending()?;
        }
        Ok(response)
    }

    fn pending(&self) -> Result<Option<PendingIntent>, String> {
        let path = self.state_dir.join("pending-intent.json");
        if !path.exists() {
            return Ok(None);
        }
        let raw = fs::read(&path).map_err(|_| "pending intent unavailable".to_string())?;
        if raw.len() > 4096 {
            return Err("pending intent exceeded its bound".into());
        }
        let pending: PendingIntent =
            serde_json::from_slice(&raw).map_err(|_| "pending intent malformed".to_string())?;
        if pending.state_id != self.state_id {
            return Err("pending intent belongs to another state".into());
        }
        if !matches!(
            pending.kind.as_str(),
            "conversation.create" | "turn.submit" | "note.remove" | "exchange.action"
        ) {
            return Err("pending mutation kind invalid".into());
        }
        let id = |s: &str, size| {
            s.len() == size
                && s.bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        };
        if !id(&pending.mutation_key, 32)
            || !id(&pending.intent_sha256, 64)
            || pending.target_id.as_ref().is_some_and(|v| !id(v, 32))
            || pending.predecessor_id.as_ref().is_some_and(|v| !id(v, 32))
            || pending.note_id.as_ref().is_some_and(|v| !id(v, 32))
            || pending.exchange_id.as_ref().is_some_and(|v| !id(v, 32))
            || (pending.kind == "exchange.action") != pending.exchange_id.is_some()
            || (pending.kind != "conversation.create") != pending.target_id.is_some()
            || (pending.kind != "turn.submit" && pending.predecessor_id.is_some())
            || (pending.kind == "note.remove") != pending.note_id.is_some()
        {
            return Err("pending intent identity malformed".into());
        }
        Ok(Some(pending))
    }

    fn check_receipt(
        &self,
        pending: &PendingIntent,
        data: &Value,
        lookup: bool,
    ) -> Result<(), String> {
        if data["mutation_key"].as_str() != Some(&pending.mutation_key)
            || data["intent_sha256"].as_str() != Some(&pending.intent_sha256)
            || (lookup && data["kind"].as_str() != Some(&pending.kind))
            || pending
                .target_id
                .as_ref()
                .is_some_and(|target| data["conversation_id"].as_str() != Some(target))
            || pending
                .exchange_id
                .as_ref()
                .is_some_and(|id| data["turn_id"].as_str() != Some(id))
            || pending
                .note_id
                .as_ref()
                .is_some_and(|note| data["note_id"].as_str() != Some(note))
        {
            return Err("mutation receipt does not match the recorded intent".into());
        }
        Ok(())
    }

    pub fn record_rendered(&self, evidence: Value) -> Result<(), String> {
        let (title, marker, width, height, phase) = validate_rendered_evidence(&evidence)?;
        let record = json!({"state_id":self.state_id,"schema_sha256":wire::SCHEMA_SHA256,
            "title":title,"marker":marker,"width":width,"height":height,"phase":phase,
            "observed_unix_ms":SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis()});
        let encoded =
            serde_json::to_vec(&record).map_err(|_| "rendered evidence unavailable".to_string())?;
        let target = self.state_dir.join("rendered-session.json");
        write_private(&target, &encoded)
    }

    async fn request_without_marker(&mut self, request: Value) -> Result<Value, String> {
        transport::exchange(self.port, &self.token, &request).await
    }

    fn write_pending(&self, kind: &str, request: &Value) -> Result<(), String> {
        if self.state_dir.join("pending-intent.json").exists() {
            return Err("pending mutation unresolved; reconcile before another mutation".into());
        }
        let arguments = request
            .get("arguments")
            .and_then(Value::as_object)
            .ok_or("mutation arguments missing")?;
        let pending = PendingIntent {
            state_id: self.state_id.clone(),
            kind: kind.to_string(),
            mutation_key: arguments
                .get("mutation_key")
                .and_then(Value::as_str)
                .ok_or("mutation key missing")?
                .to_string(),
            target_id: arguments
                .get("conversation_id")
                .and_then(Value::as_str)
                .map(str::to_string),
            predecessor_id: arguments
                .get("expected_previous_turn_id")
                .and_then(Value::as_str)
                .map(str::to_string),
            note_id: arguments
                .get("note_id")
                .and_then(Value::as_str)
                .map(str::to_string),
            exchange_id: arguments
                .get("turn_id")
                .and_then(Value::as_str)
                .map(str::to_string),
            intent_sha256: if kind == "conversation.create" {
                self.creation_intent.clone()
            } else if kind == "exchange.action" {
                sha256_hex(&serde_json::to_vec(&json!({"conversation_id":arguments["conversation_id"],
                    "turn_id":arguments["turn_id"],"action":arguments["action"],"text":arguments["text"]}))
                    .map_err(|_| "mutation intent unavailable".to_string())?)
            } else if kind == "note.remove" {
                sha256_hex(
                    &serde_json::to_vec(&json!({"conversation_id":arguments["conversation_id"],
                    "note_id":arguments["note_id"],"preview_token":arguments["preview_token"]}))
                    .map_err(|_| "mutation intent unavailable".to_string())?,
                )
            } else {
                let mut intent = json!({"conversation_id":arguments["conversation_id"],
                    "expected_previous_turn_id":arguments["expected_previous_turn_id"],"text":arguments["text"]});
                if let Some(skill) = arguments.get("skill").filter(|value| !value.is_null()) {
                    intent["skill"] = skill.clone();
                }
                sha256_hex(
                    &serde_json::to_vec(&intent)
                        .map_err(|_| "mutation intent unavailable".to_string())?,
                )
            },
        };
        let encoded =
            serde_json::to_vec(&pending).map_err(|_| "pending intent malformed".to_string())?;
        if encoded.len() > 4096 {
            return Err("pending intent exceeded its bound".into());
        }
        let target = self.state_dir.join("pending-intent.json");
        write_private(&target, &encoded)
    }

    fn clear_pending(&self) -> Result<(), String> {
        let path = self.state_dir.join("pending-intent.json");
        if path.exists() {
            private_file::clear(&path, &self.state_dir.join("cleared-intent.json"))
                .map_err(|error| error.to_string())?;
        }
        Ok(())
    }
}

fn request_id() -> String {
    let count = REQUEST_COUNTER.fetch_add(1, Ordering::Relaxed);
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    sha256_hex(format!("{}:{now}:{count}", std::process::id()).as_bytes())[..32].to_string()
}

// Small local SHA-256 keeps the pending marker dependency-free.
pub(crate) fn sha256_hex(input: &[u8]) -> String {
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
        0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
        0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
        0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
        0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
        0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
        0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
        0xc67178f2,
    ];
    let mut data = input.to_vec();
    let bits = (data.len() as u64) * 8;
    data.push(0x80);
    while data.len() % 64 != 56 {
        data.push(0)
    }
    data.extend_from_slice(&bits.to_be_bytes());
    let mut h = [
        0x6a09e667u32,
        0xbb67ae85,
        0x3c6ef372,
        0xa54ff53a,
        0x510e527f,
        0x9b05688c,
        0x1f83d9ab,
        0x5be0cd19,
    ];
    for chunk in data.chunks_exact(64) {
        let mut w = [0u32; 64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes(chunk[i * 4..i * 4 + 4].try_into().unwrap())
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1)
        }
        let (mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut x) =
            (h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7]);
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ (!e & g);
            let t1 = x
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            x = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2)
        }
        for (i, v) in [a, b, c, d, e, f, g, x].iter().enumerate() {
            h[i] = h[i].wrapping_add(*v)
        }
    }
    h.iter().map(|v| format!("{v:08x}")).collect()
}








