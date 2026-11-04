//! Explicit user-selected document copies. Never a model tool or an overwrite.
use base64::Engine;
const LIMIT: usize = 32 * 1024 * 1024;

fn decode(data: &str) -> Result<Vec<u8>, String> {
    if data.len() > LIMIT.div_ceil(3) * 4 {
        return Err("PDF copies must be at most 32 MB.".into());
    }
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(data)
        .map_err(|_| "Invalid PDF copy.".to_string())?;
    if bytes.len() > LIMIT || !bytes.starts_with(b"%PDF-") {
        return Err("The reader did not supply a supported PDF copy.".into());
    }
    Ok(bytes)
}

fn write_new(path: &std::path::Path, bytes: &[u8], extension: &str) -> Result<String, String> {
    use std::{
        fs::{self, OpenOptions},
        io::Write,
    };
    let parent = path
        .parent()
        .ok_or("Choose a destination folder.")?
        .canonicalize()
        .map_err(|_| "The destination folder is unavailable.")?;
    let name = path.file_name().ok_or("Choose a document filename.")?;
    let target = parent.join(name);
    if target
        .extension()
        .and_then(|s| s.to_str())
        .is_none_or(|s| !s.eq_ignore_ascii_case(extension))
    {
        return Err(format!("Choose a filename ending in .{extension}."));
    }
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|_| "The system clock is unavailable.")?
        .as_nanos();
    let temporary = parent.join(format!(
        ".kejtu-document-copy-{}-{stamp}",
        std::process::id()
    ));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options
        .open(&temporary)
        .map_err(|_| "A temporary copy could not be created.")?;
    let result = (|| {
        file.write_all(bytes)
            .and_then(|_| file.sync_all())
            .map_err(|_| "The document copy could not be fully written.")?;
        // The destination appears complete in one step. hard_link refuses existing
        // names (including symlinks); cancellation/failure never truncates a file.
        fs::hard_link(&temporary, &target).map_err(|e| {
            if e.kind() == std::io::ErrorKind::AlreadyExists {
                "That filename already exists. Choose a new name; the original was not changed."
            } else {
                "The document copy could not be placed in that folder."
            }
        })?;
        Ok(target.to_string_lossy().into_owned())
    })();
    drop(file);
    let _ = fs::remove_file(temporary);
    result
}

#[tauri::command]
pub async fn save_pdf_copy(
    app: tauri::AppHandle,
    name: String,
    data: String,
    initial: Option<String>,
) -> Result<Option<String>, String> {
    save_copy(app, name, decode(&data)?, "pdf", initial).await
}

fn decode_docx(data: &str) -> Result<Vec<u8>, String> {
    if data.len() > LIMIT.div_ceil(3) * 4 {
        return Err("DOCX copies must be at most 32 MB.".into());
    }
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(data)
        .map_err(|_| "Invalid DOCX copy.")?;
    // Format guard only. WordCraft owns the package; this writer never extracts it.
    if bytes.len() > LIMIT
        || !bytes.starts_with(b"PK\x03\x04")
        || !bytes.windows(17).any(|w| w == b"word/document.xml")
    {
        return Err("The editor did not supply a DOCX copy.".into());
    }
    Ok(bytes)
}

#[tauri::command]
pub async fn save_docx_copy(
    app: tauri::AppHandle,
    name: String,
    data: String,
    initial: Option<String>,
) -> Result<Option<String>, String> {
    save_copy(app, name, decode_docx(&data)?, "docx", initial).await
}

fn decode_markdown(data: &str) -> Result<Vec<u8>, String> {
    if data.len() > LIMIT.div_ceil(3) * 4 {
        return Err("Markdown copies must be at most 32 MB.".into());
    }
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(data)
        .map_err(|_| "Invalid Markdown copy.".to_string())?;
    // Empty documents are valid. Never write binary data under a text extension.
    if bytes.len() > LIMIT || bytes.contains(&0) || std::str::from_utf8(&bytes).is_err() {
        return Err("The editor did not supply UTF-8 Markdown text.".into());
    }
    Ok(bytes)
}

#[tauri::command]
pub async fn save_markdown_copy(
    app: tauri::AppHandle,
    name: String,
    data: String,
    initial: Option<String>,
) -> Result<Option<String>, String> {
    save_copy(app, name, decode_markdown(&data)?, "md", initial).await
}

// A chooser hint never grants file access or authorizes a write. Only an
// existing absolute directory is useful; never fall back to the process cwd.
pub(crate) fn save_directory(
    project: Option<&std::path::Path>,
    documents: Option<&std::path::Path>,
    home: Option<&std::path::Path>,
) -> Option<std::path::PathBuf> {
    [project, documents, home]
        .into_iter()
        .flatten()
        .find(|path| path.is_absolute() && path.is_dir())
        .map(std::path::Path::to_path_buf)
}

#[cfg(target_os = "linux")]
fn save_dialog(
    parent: Option<&gtk::Window>,
    name: &str,
    extension: &str,
    directory: &std::path::Path,
) -> Result<gtk::FileChooserNative, String> {
    use gtk::prelude::*;
    let dialog = gtk::FileChooserNative::new(
        Some(match extension {
            "pdf" => "Save a PDF copy",
            "md" => "Save a Markdown copy",
            _ => "Save a DOCX copy",
        }),
        parent,
        gtk::FileChooserAction::Save,
        Some("Save copy"),
        Some("Cancel"),
    );
    dialog.set_modal(true);
    if !dialog.set_current_folder(&directory) {
        return Err(
            "The save folder is unavailable. Choose an available project folder and try again."
                .to_string(),
        );
    }
    let stem = std::path::Path::new(&name)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("document")
        .chars()
        .filter(|c| !c.is_control())
        .take(120)
        .collect::<String>();
    dialog.set_current_name(&format!("{stem}-copy.{extension}"));
    let filter = gtk::FileFilter::new();
    filter.set_name(Some(match extension {
        "pdf" => "PDF document",
        "md" => "Markdown document",
        _ => "Word document",
    }));
    filter.add_pattern(&format!("*.{extension}"));
    dialog.add_filter(filter);
    Ok(dialog)
}

async fn save_copy(
    app: tauri::AppHandle,
    name: String,
    bytes: Vec<u8>,
    extension: &'static str,
    initial: Option<String>,
) -> Result<Option<String>, String> {
    #[cfg(target_os = "linux")]
    {
        use gtk::prelude::*;
        use std::cell::RefCell;
        use tauri::Manager;
        let directory = save_directory(
            initial.as_deref().map(std::path::Path::new),
            app.path().document_dir().ok().as_deref(),
            app.path().home_dir().ok().as_deref(),
        )
        .ok_or("No available save folder. Check that your home folder is accessible.")?;
        let (sender, receiver) = tokio::sync::oneshot::channel();
        let handle = app.clone();
        app.run_on_main_thread(move || {
            let parent = handle
                .get_webview_window("main")
                .and_then(|w| w.gtk_window().ok());
            let dialog = match save_dialog(
                parent.as_ref().map(|p| p.upcast_ref::<gtk::Window>()),
                &name,
                extension,
                &directory,
            ) {
                Ok(dialog) => dialog,
                Err(error) => {
                    let _ = sender.send(Err(error));
                    return;
                }
            };
            let sender = RefCell::new(Some(sender));
            let retained = RefCell::new(Some(dialog.clone()));
            dialog.connect_response(move |dialog, response| {
                let path = if response == gtk::ResponseType::Accept {
                    dialog.filename()
                } else {
                    None
                };
                if let Some(send) = sender.borrow_mut().take() {
                    let _ = send.send(Ok(path));
                }
                dialog.destroy();
                retained.borrow_mut().take();
            });
            dialog.show();
        })
        .map_err(|_| "The save chooser could not open.")?;
        let path = receiver
            .await
            .map_err(|_| "The save chooser closed unexpectedly.")??;
        match path {
            Some(path) => {
                tauri::async_runtime::spawn_blocking(move || write_new(&path, &bytes, extension))
                    .await
                    .map_err(|_| "The document copy could not be saved.")?
                    .map(Some)
            }
            None => Ok(None),
        }
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = (app, name, bytes, extension, initial);
        Err("Document copy export is not yet available on this platform. Your copy remains open in the reader.".into())
    }
}


