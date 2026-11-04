//! Explicit portable-test startup. Installed builds never fall back to the build tree.
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
            return Err("Package and test data paths must not use links or junctions.".into());
        }
    }
    if info.file_type().is_symlink()
        || (directory && !info.is_dir())
        || (!directory && !info.is_file())
    {
        return Err("Package and test data paths must be regular files and directories.".into());
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
        Err(_) => Err("Test data could not be inspected; nothing was initialized.".into()),
        Ok(_) => {
            ordinary(path, directory)?;
            Ok(true)
        }
    }
}

pub(crate) fn installed() -> Result<Option<Startup>, String> {
    if env!("KEJTU_PACKAGE_KIND") == "development" {
        return Ok(None);
    }
    #[cfg(not(windows))]
    return Err("This test package is for Windows.".into());
    #[cfg(windows)]
    {
        let executable = std::env::current_exe().map_err(|_| "Package location is unavailable.")?;
        let local = std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .ok_or("Windows local application data is unavailable.")?;
        load(&executable, &local).map(Some)
    }
}

fn load(executable: &Path, local: &Path) -> Result<Startup, String> {
    if !executable.is_absolute() || !local.is_absolute() {
        return Err("Package and local application data locations must be absolute.".into());
    }
    parents(executable)?;
    ordinary(executable, false)?;
    parents(local)?;
    ordinary(local, true)?;
    let root = executable
        .parent()
        .ok_or("Package location is unavailable.")?;
    let manifest: Manifest = serde_json::from_slice(&bounded(
        &root.join("kejtu-test-package.json"),
        8 * 1024 * 1024,
    )?)
    .map_err(|_| {
        "The test package manifest is missing or invalid. Extract the complete ZIP again."
    })?;
    if manifest.schema != 1
        || !hex(&manifest.source.commit, 40)
        || !hex(&manifest.source.tree, 40)
        || manifest.native.path != "Kejtu.exe"
        || executable.file_name() != Some(std::ffi::OsStr::new("Kejtu.exe"))
        || !hex(&manifest.native.sha256, 64)
        || manifest.native.bytes == 0
        || manifest.files.len() > 20000
    {
        return Err("The test package manifest does not describe this build.".into());
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
            || !seen.insert(entry.path.to_ascii_lowercase())
        {
            return Err("The test package contains an invalid or duplicate file record.".into());
        }
    }
    for name in [
        ".venv/Scripts/python.exe",
        "src/kejtu_next/__init__.py",
        "contracts/native-v1.json",
        "package-defaults/profile.json",
        "package-defaults/standing-profile.md",
    ] {
        let entry = manifest
            .files
            .iter()
            .find(|entry| entry.path == name)
            .ok_or("The test package is incomplete. Extract the complete ZIP again.")?;
        let path = root.join(name);
        parents(&path)?;
        ordinary(&path, false)?;
        // Defaults are launch inputs. The backend handshake separately verifies
        // every Python source and the native contract using the existing owner.
        if name.starts_with("package-defaults/") {
            let raw = bounded(&path, 16384)?;
            if raw.len() as u64 != entry.bytes || crate::backend::sha256_hex(&raw) != entry.sha256 {
                return Err(
                    "The test package launch defaults changed. Extract the complete ZIP again."
                        .into(),
                );
            }
        }
    }
    let data = local.join("Kejtu-Test");
    if present(&data, true)? {
        parents(&data)?;
    } else {
        fs::create_dir(&data).map_err(|_| "Kejtu Test data directory could not be created.")?;
        ordinary(&data, true)?;
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
                    .map_err(|_| "Test data could not be inspected.")?
                    .next()
                    .is_some()
            {
                return Err("Kejtu Test has incomplete existing data. It was preserved; initialization was refused.".into());
            }
            true
        }
        _ => {
            return Err(
                "Kejtu Test archive and identity do not match. Existing data was preserved.".into(),
            )
        }
    };
    Ok(Startup {
        root: root.to_path_buf(),
        state,
        initialize,
        profile: root.join("package-defaults/profile.json"),
        standing: root.join("package-defaults/standing-profile.md"),
    })
}


