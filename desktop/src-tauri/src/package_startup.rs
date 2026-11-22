//! Explicit installed startup: the Windows portable test and the Linux package.
//! Installed builds never fall back to the build tree. Each layout names its
//! own files and wording; the checks below are shared.
use serde::Deserialize;
use std::collections::HashSet;
use std::fs;
use std::io::Read;
use std::path::{Component, Path, PathBuf};

pub(crate) struct Startup {
    pub(crate) root: PathBuf,
    pub(crate) state: PathBuf,
    pub(crate) profile: PathBuf,
    pub(crate) standing: PathBuf,
    pub(crate) initialize: bool,
    /// The source commit the verified manifest names.
    pub(crate) commit: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Source {
    commit: String,
    tree: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Entry {
    path: String,
    bytes: u64,
    sha256: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    schema: u32,
    source: Source,
    native: Entry,
    files: Vec<Entry>,
}

/// What differs between installed layouts.
pub(crate) struct Layout {
    /// Hashed file list beside the program files.
    manifest: &'static str,
    /// The native executable, relative to the program folder.
    native: &'static str,
    python: &'static str,
    /// The private data folder's name inside the platform data location.
    data: &'static str,
    /// How messages name the package and its data.
    package: &'static str,
    name: &'static str,
    repair: &'static str,
    /// Windows folds case, so two names that differ only by case collide there.
    case_insensitive: bool,
}

#[cfg_attr(not(windows), allow(dead_code))]
pub(crate) const WINDOWS_TEST: Layout = Layout {
    manifest: "kejtu-test-package.json",
    native: "Kejtu.exe",
    python: ".venv/Scripts/python.exe",
    data: "Kejtu-Test",
    package: "test package",
    name: "Kejtu Test",
    repair: "Extract the complete ZIP again.",
    case_insensitive: true,
};

/// Program files read-only under one folder (normally /opt/kejtu); private data
/// in the user's data location (`$XDG_DATA_HOME`, else ~/.local/share).
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
pub(crate) const LINUX: Layout = Layout {
    manifest: "kejtu-package.json",
    native: "bin/kejtu",
    python: ".venv/bin/python",
    data: "kejtu",
    package: "Kejtu package",
    name: "Kejtu",
    repair: "Reinstall the Kejtu package.",
    case_insensitive: false,
};

fn hex(value: &str, size: usize) -> bool {
    value.len() == size
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn ordinary(path: &Path, directory: bool) -> Result<(), String> {
    let info =
        fs::symlink_metadata(path).map_err(|_| "Required package or data path is unavailable.")?;
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if info.file_attributes() & 0x400 != 0 {
            return Err("Package and data paths must not use links or junctions.".into());
        }
    }
    if info.file_type().is_symlink()
        || (directory && !info.is_dir())
        || (!directory && !info.is_file())
    {
        return Err("Package and data paths must be regular files and directories.".into());
    }
    Ok(())
}

fn parents(path: &Path) -> Result<(), String> {
    for directory in path.ancestors().skip(1) {
        ordinary(directory, true)?;
    }
    Ok(())
}

fn bounded(path: &Path, maximum: u64) -> Result<Vec<u8>, String> {
    parents(path)?;
    ordinary(path, false)?;
    let mut bytes = Vec::new();
    fs::File::open(path)
        .and_then(|file| file.take(maximum + 1).read_to_end(&mut bytes))
        .map_err(|_| "Package input could not be read.")?;
    if bytes.len() as u64 > maximum {
        return Err("Package input exceeds its supported size.".into());
    }
    Ok(bytes)
}

fn present(path: &Path, directory: bool) -> Result<bool, String> {
    match fs::symlink_metadata(path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(_) => Err("Existing data could not be inspected; nothing was initialized.".into()),
        Ok(_) => {
            ordinary(path, directory)?;
            Ok(true)
        }
    }
}

pub(crate) fn installed() -> Result<Option<Startup>, String> {
    let kind = env!("KEJTU_PACKAGE_KIND");
    if kind == "development" {
        return Ok(None);
    }
    let executable = std::env::current_exe().map_err(|_| "Package location is unavailable.")?;
    #[cfg(windows)]
    if kind == "windows-test-portable" {
        let local = std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .ok_or("Windows local application data is unavailable.")?;
        return load(&executable, &local, &WINDOWS_TEST).map(Some);
    }
    #[cfg(target_os = "linux")]
    if kind == "linux-installed" {
        let startup = load(&executable, &linux_data_location()?, &LINUX)?;
        // Not while dpkg or pacman is replacing these files (or a transaction is open).
        crate::installed_update::startup_check(&startup.root, &startup.commit)?;
        return Ok(Some(startup));
    }
    let _ = executable;
    Err("This Kejtu build is not for this system.".into())
}

/// The same rule Tauri and desktop_paths.py use: an absolute `XDG_DATA_HOME`,
/// otherwise ~/.local/share. Resolved once, so a distribution's linked /home
/// is accepted while Kejtu's own folders below it must stay real directories.
#[cfg(target_os = "linux")]
fn linux_data_location() -> Result<PathBuf, String> {
    let base = match std::env::var_os("XDG_DATA_HOME").map(PathBuf::from) {
        Some(path) if path.is_absolute() => path,
        _ => {
            let home = std::env::var_os("HOME")
                .filter(|value| !value.is_empty())
                .map(PathBuf::from)
                .ok_or("Your home folder is unavailable.")?;
            if !home.is_absolute() {
                return Err("Your home folder location must be absolute.".into());
            }
            home.join(".local/share")
        }
    };
    fs::create_dir_all(&base).map_err(|_| "Your data location could not be created.")?;
    fs::canonicalize(&base).map_err(|_| "Your data location is unavailable.".into())
}

fn create_private_directory(path: &Path) -> std::io::Result<()> {
    let mut builder = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder.create(path)
}

fn load(executable: &Path, local: &Path, layout: &Layout) -> Result<Startup, String> {
    if !executable.is_absolute() || !local.is_absolute() {
        return Err("Package and local application data locations must be absolute.".into());
    }
    parents(executable)?;
    ordinary(executable, false)?;
    parents(local)?;
    ordinary(local, true)?;
    let root = executable
        .ancestors()
        .nth(Path::new(layout.native).components().count())
        .ok_or("Package location is unavailable.")?;
    let manifest: Manifest =
        serde_json::from_slice(&bounded(&root.join(layout.manifest), 8 * 1024 * 1024)?).map_err(
            |_| {
                format!(
                    "The {} manifest is missing or invalid. {}",
                    layout.package, layout.repair
                )
            },
        )?;
    if manifest.schema != 1
        || !hex(&manifest.source.commit, 40)
        || !hex(&manifest.source.tree, 40)
        || manifest.native.path != layout.native
        || root.join(layout.native) != executable
        || !hex(&manifest.native.sha256, 64)
        || manifest.native.bytes == 0
        || manifest.files.len() > 50000
    {
        return Err(format!(
            "The {} manifest does not describe this build.",
            layout.package
        ));
    }
    let mut seen = HashSet::new();
    for entry in &manifest.files {
        let path = Path::new(&entry.path);
        if entry.path.is_empty()
            || entry.path.contains('\\')
            || entry.path.contains(':')
            || entry
                .path
                .split('/')
                .any(|part| part.is_empty() || part == "." || part == "..")
            || !path
                .components()
                .all(|part| matches!(part, Component::Normal(_)))
            || !hex(&entry.sha256, 64)
            || !seen.insert(if layout.case_insensitive {
                entry.path.to_ascii_lowercase()
            } else {
                entry.path.clone()
            })
        {
            return Err(format!(
                "The {} contains an invalid or duplicate file record.",
                layout.package
            ));
        }
    }
    for name in [
        layout.python,
        "src/kejtu_next/__init__.py",
        "contracts/native-v1.json",
        "package-defaults/profile.json",
        "package-defaults/standing-profile.md",
    ] {
        let entry = manifest
            .files
            .iter()
            .find(|entry| entry.path == name)
            .ok_or_else(|| format!("The {} is incomplete. {}", layout.package, layout.repair))?;
        let path = root.join(name);
        parents(&path)?;
        ordinary(&path, false)?;
        // Defaults are launch inputs. The backend handshake separately verifies
        // every Python source and the native contract using the existing owner.
        if name.starts_with("package-defaults/") {
            let raw = bounded(&path, 16384)?;
            if raw.len() as u64 != entry.bytes || crate::backend::sha256_hex(&raw) != entry.sha256 {
                return Err(format!(
                    "The {} launch defaults changed. {}",
                    layout.package, layout.repair
                ));
            }
        }
    }
    let data = local.join(layout.data);
    if present(&data, true)? {
        parents(&data)?;
    } else {
        create_private_directory(&data)
            .map_err(|_| format!("{} data directory could not be created.", layout.name))?;
        ordinary(&data, true)?;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let owner = fs::metadata(&data)
            .map_err(|_| "Kejtu data is unavailable.")?
            .uid();
        if owner != unsafe { libc::getuid() } {
            return Err(format!(
                "{} data must belong to you. It was not changed.",
                layout.name
            ));
        }
    }
    let state = data.join("runtime");
    let has_state = present(&state, true)?;
    let has_pin = present(&data.join("runtime.identity.json"), false)?;
    let has_archive = if has_state {
        present(&state.join("core.sqlite3"), false)?
    } else {
        false
    };
    let initialize = match (has_pin, has_archive) {
        (true, true) => false,
        (false, false) => {
            if has_state
                && fs::read_dir(&state)
                    .map_err(|_| format!("{} data could not be inspected.", layout.name))?
                    .next()
                    .is_some()
            {
                return Err(format!("{} has incomplete existing data. It was preserved; initialization was refused.", layout.name));
            }
            true
        }
        _ => {
            return Err(format!(
                "{} archive and identity do not match. Existing data was preserved.",
                layout.name
            ))
        }
    };
    Ok(Startup {
        root: root.to_path_buf(),
        state,
        initialize,
        profile: root.join("package-defaults/profile.json"),
        standing: root.join("package-defaults/standing-profile.md"),
        commit: manifest.source.commit,
    })
}


