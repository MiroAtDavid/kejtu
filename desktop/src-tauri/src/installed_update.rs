//! Package updates of the installed Linux app, on the native side:
//! - Do not start while dpkg or pacman is replacing Kejtu's files.
//! - Relaunch the new version when the person chooses it.
//! The backend holds work and reports the update (application/installed_update.py).
//! Both sides apply one rule for "in progress / complete / unchanged", checked against
//! contracts/fixtures/installed-package-state.json.
#![cfg_attr(not(target_os = "linux"), allow(dead_code))]
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::{Duration, Instant};

const MANIFEST: &str = "kejtu-package.json";
const WAIT_FOR_PREVIOUS: Duration = Duration::from_secs(30);

/// The program folder and the commit this process was started from.
struct Running {
    root: PathBuf,
    commit: String,
}
static RUNNING: OnceLock<Running> = OnceLock::new();

#[derive(Debug, PartialEq)]
pub(crate) enum Verdict {
    Unchanged,
    Complete,
    InProgress,
}

pub(crate) struct Record {
    settled: bool,
    version: Option<String>,
}

fn dpkg(text: &str) -> Option<Record> {
    for stanza in text.split("\n\n") {
        let field = |name: &str| {
            stanza.lines().find_map(|line| {
                line.strip_prefix(name)
                    .and_then(|rest| rest.strip_prefix(':'))
                    .map(|value| value.trim().to_string())
            })
        };
        if field("Package").as_deref() == Some("kejtu") {
            return Some(Record {
                settled: field("Status").as_deref() == Some("install ok installed"),
                version: field("Version").filter(|value| !value.is_empty()),
            });
        }
    }
    None
}

fn pacman_desc(text: &str) -> (Option<String>, Option<String>) {
    let (mut name, mut version, mut key) = (None, None, "");
    for line in text.lines() {
        if line.len() > 2 && line.starts_with('%') && line.ends_with('%') {
            key = &line[1..line.len() - 1];
        } else if !line.is_empty() {
            match key {
                "NAME" if name.is_none() => name = Some(line.to_string()),
                "VERSION" if version.is_none() => version = Some(line.to_string()),
                _ => {}
            }
        }
    }
    (name, version)
}

/// The kejtu package as dpkg or pacman records it; None for an unpacked folder.
pub(crate) fn package_record(system: &Path) -> Option<Record> {
    if let Ok(text) = fs::read_to_string(system.join("var/lib/dpkg/status")) {
        if let Some(record) = dpkg(&text) {
            return Some(record);
        }
    }
    let entries = fs::read_dir(system.join("var/lib/pacman/local")).ok()?;
    let versions: Vec<Option<String>> = entries
        .flatten()
        .filter(|entry| entry.file_name().to_string_lossy().starts_with("kejtu-"))
        .filter_map(|entry| fs::read_to_string(entry.path().join("desc")).ok())
        .map(|text| pacman_desc(&text))
        .filter(|(name, _)| name.as_deref() == Some("kejtu"))
        .map(|(_, version)| version)
        .collect();
    if versions.is_empty() {
        return None;
    }
    Some(Record {
        settled: versions.len() == 1 && !system.join("var/lib/pacman/db.lck").exists(),
        version: if versions.len() == 1 {
            versions[0].clone()
        } else {
            None
        },
    })
}

fn names_commit(version: &str, commit: &str) -> bool {
    if commit.len() < 7 {
        return false;
    }
    let needle = format!(".g{}", &commit[..7]);
    version.match_indices(&needle).any(|(at, _)| {
        matches!(
            version[at + needle.len()..].chars().next(),
            None | Some('-')
        )
    })
}

pub(crate) fn verdict(record: Option<&Record>, manifest: Option<&str>, running: &str) -> Verdict {
    if let Some(record) = record {
        let names = match (record.version.as_deref(), manifest) {
            (Some(version), Some(commit)) => names_commit(version, commit),
            _ => false,
        };
        if !record.settled || !names {
            return Verdict::InProgress;
        }
    }
    match manifest {
        None => Verdict::InProgress,
        Some(commit) if commit == running => Verdict::Unchanged,
        Some(_) => Verdict::Complete,
    }
}

fn manifest_commit(root: &Path) -> Option<String> {
    let raw = fs::read(root.join(MANIFEST)).ok()?;
    let value: serde_json::Value = serde_json::from_slice(&raw).ok()?;
    value["source"]["commit"].as_str().map(str::to_string)
}

fn process_start(pid: u32) -> Option<String> {
    let stat = fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    stat.rsplit_once(')')?
        .1
        .split_whitespace()
        .nth(19)
        .map(str::to_string)
}

/// Startup of the installed app: refuse while the package manager is replacing these
/// files, then remember where this process came from for a later relaunch.
pub(crate) fn startup_check(root: &Path, commit: &str) -> Result<(), String> {
    check_at(Path::new("/"), root, commit)?;
    let _ = RUNNING.set(Running {
        root: root.to_path_buf(),
        commit: commit.to_string(),
    });
    Ok(())
}

fn check_at(system: &Path, root: &Path, commit: &str) -> Result<(), String> {
    let _ = root;
    if verdict(package_record(system).as_ref(), Some(commit), commit) == Verdict::InProgress {
        return Err(
            "Kejtu is being updated, or a package update is still running. \
                    Open Kejtu again when it has finished."
                .into(),
        );
    }
    Ok(())
}

/// A relaunched instance (`--after-update PID:START`) waits until the previous process
/// has released its archive and single-instance name by exiting; bounded.
pub(crate) fn wait_for_previous(args: impl Iterator<Item = String>) {
    let args: Vec<String> = args.collect();
    let Some(at) = args.iter().position(|arg| arg == "--after-update") else {
        return;
    };
    let Some((pid, start)) = args.get(at + 1).and_then(|value| value.split_once(':')) else {
        return;
    };
    let Ok(pid) = pid.parse::<u32>() else {
        return;
    };
    let end = Instant::now() + WAIT_FOR_PREVIOUS;
    while process_start(pid).as_deref() == Some(start) && Instant::now() < end {
        std::thread::sleep(Duration::from_millis(100));
    }
}

/// Start the new program detached, in its own session so it does not end with this one.
/// It waits for exactly this process to exit (wait_for_previous) before it starts.
#[cfg(target_os = "linux")]
fn start_new(program: &Path, me: u32, start: &str) -> Result<(), String> {
    use std::os::unix::process::CommandExt;
    use std::process::{Command, Stdio};
    let mut command = Command::new(program);
    command
        .arg("--after-update")
        .arg(format!("{me}:{start}"))
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    if let Some(home) = std::env::var_os("HOME") {
        command.current_dir(home);
    }
    unsafe {
        command.pre_exec(|| {
            libc::setsid();
            Ok(())
        });
    }
    command.spawn().map(drop).map_err(|error| {
        format!("The new version could not be started ({error}). Kejtu stays open; try again or open it from the menu.")
    })
}

/// Relaunch to update: only when the installation is verified complete. The new program
/// starts first; only if that worked is the backend stopped (joined, releasing the
/// archive), the single-instance name released and this process ended. If starting
/// fails, nothing is closed and the window stays usable as it was.
#[cfg(target_os = "linux")]
#[tauri::command]
pub(crate) async fn relaunch_after_update(
    app: tauri::AppHandle,
    state: tauri::State<'_, crate::Session>,
) -> Result<(), String> {
    let running = RUNNING
        .get()
        .ok_or("Relaunch to update applies to the installed app only.")?;
    let manifest = manifest_commit(&running.root);
    if verdict(
        package_record(Path::new("/")).as_ref(),
        manifest.as_deref(),
        &running.commit,
    ) != Verdict::Complete
    {
        return Err("The update has not finished yet.".into());
    }
    let me = std::process::id();
    let start = process_start(me).ok_or("This process could not be identified.")?;
    start_new(&running.root.join("bin/kejtu"), me, &start)?;
    let owner = std::mem::replace(
        &mut *state.lock().await,
        Err("native session closed".into()),
    );
    let _ = tauri::async_runtime::spawn_blocking(move || drop(owner)).await;
    tauri_plugin_single_instance::destroy(&app);
    app.exit(0);
    Ok(())
}


