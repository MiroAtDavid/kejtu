//! Explicit OS image paste/drop only. No path-based file reader is exposed to JS.
use base64::Engine;
use serde::Serialize;
use std::io::Read;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{Emitter, Manager};

const MAX_BYTES: usize = 1_400_000;
const MAX_IMAGES: usize = 32;
const DROP_LIFETIME: Duration = Duration::from_secs(30);

#[derive(Default)]
pub struct ImageInput(Mutex<Drops>);

#[derive(Default)]
struct Drops {
    sequence: u64,
    current: Option<DropGrant>,
}

struct DropGrant {
    token: String,
    created: Instant,
    paths: Vec<Option<PathBuf>>,
}

impl Drops {
    fn offer(&mut self, paths: Vec<PathBuf>, now: Instant) -> Result<String, String> {
        self.current = None;
        if paths.is_empty() || paths.len() > MAX_IMAGES {
            return Err("Drop between 1 and 32 PNG or JPEG images.".into());
        }
        self.sequence = self
            .sequence
            .checked_add(1)
            .ok_or("Image drop counter exhausted.")?;
        let token = format!("{}-{}", std::process::id(), self.sequence);
        self.current = Some(DropGrant {
            token: token.clone(),
            created: now,
            paths: paths.into_iter().map(Some).collect(),
        });
        Ok(token)
    }

    fn take(&mut self, token: &str, index: usize, now: Instant) -> Result<PathBuf, String> {
        let grant = self.current.as_mut().ok_or("Drop the image again.")?;
        if now.duration_since(grant.created) > DROP_LIFETIME {
            self.current = None;
            return Err("The image drop expired. Drop the image again.".into());
        }
        if grant.token != token {
            return Err("This image drop is no longer current.".into());
        }
        grant
            .paths
            .get_mut(index)
            .and_then(Option::take)
            .ok_or_else(|| "This dropped image is unavailable. Drop it again.".into())
    }
}

fn image_url(bytes: &[u8]) -> Result<String, String> {
    if bytes.is_empty() || bytes.len() > MAX_BYTES {
        return Err("Choose an image smaller than 1.4 MB.".into());
    }
    let mime = if bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        "image/png"
    } else if bytes.starts_with(b"\xff\xd8\xff") {
        "image/jpeg"
    } else {
        return Err("Choose a PNG or JPEG image.".into());
    };
    // Preserve the OS-provided bytes; the existing ingress validates the container.
    Ok(format!(
        "data:{mime};base64,{}",
        base64::engine::general_purpose::STANDARD.encode(bytes)
    ))
}

fn read_image(path: PathBuf) -> Result<String, String> {
    if !path.is_absolute() {
        return Err("Drop a local PNG or JPEG file.".into());
    }
    let mut options = std::fs::OpenOptions::new();
    options.read(true);
    // An explicitly dropped special file must not block a worker on open/read.
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NONBLOCK);
    }
    let file = options
        .open(path)
        .map_err(|_| "The dropped image could not be opened.")?;
    let info = file
        .metadata()
        .map_err(|_| "The dropped image could not be read.")?;
    if !info.is_file() {
        return Err("Drop an image file, not a folder or special file.".into());
    }
    if info.len() == 0 || info.len() > MAX_BYTES as u64 {
        return Err("Choose an image smaller than 1.4 MB.".into());
    }
    let mut bytes = Vec::with_capacity(info.len() as usize);
    file.take((MAX_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| "The dropped image could not be read.")?;
    image_url(&bytes)
}

#[derive(Clone, Serialize)]
struct DropNotice {
    kind: &'static str,
    x: f64,
    y: f64,
    token: Option<String>,
    count: usize,
    error: Option<String>,
}

pub fn connect(app: &tauri::App) {
    app.manage(ImageInput::default());
    let Some(window) = app.get_webview_window("main") else {
        return;
    };
    let handle = window.clone();
    window.on_window_event(move |event| {
        let tauri::WindowEvent::DragDrop(event) = event else {
            return;
        };
        let (kind, position, paths) = match event {
            tauri::DragDropEvent::Enter { position, .. } => ("over", Some(position), None),
            tauri::DragDropEvent::Over { position } => ("over", Some(position), None),
            tauri::DragDropEvent::Drop { paths, position } => ("drop", Some(position), Some(paths)),
            tauri::DragDropEvent::Leave => ("leave", None, None),
            _ => return,
        };
        // Wry 0.55's GTK coordinates are already logical widget coordinates.
        // Other backends supply physical coordinates, as the Tauri type promises.
        #[cfg(target_os = "linux")]
        let scale = 1.0;
        #[cfg(not(target_os = "linux"))]
        let scale = handle.scale_factor().unwrap_or(1.0);
        let mut notice = DropNotice {
            kind,
            x: position.map_or(0.0, |p| p.x / scale),
            y: position.map_or(0.0, |p| p.y / scale),
            token: None,
            count: 0,
            error: None,
        };
        if let Some(paths) = paths {
            notice.count = paths.len();
            let state = handle.state::<ImageInput>();
            match state.0.lock().unwrap().offer(paths.clone(), Instant::now()) {
                Ok(token) => notice.token = Some(token),
                Err(error) => notice.error = Some(error),
            };
        }
        let _ = handle.emit("kejtu-image-input", notice);
    });
}

#[tauri::command]
pub async fn read_dropped_image(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, ImageInput>,
    token: String,
    index: usize,
) -> Result<String, String> {
    if window.label() != "main" {
        return Err("Image input is unavailable here.".into());
    }
    let path = state
        .0
        .lock()
        .map_err(|_| "Image input is unavailable.")?
        .take(&token, index, Instant::now())?;
    tauri::async_runtime::spawn_blocking(move || read_image(path))
        .await
        .map_err(|_| "The dropped image could not be read.".to_string())?
}

#[cfg(target_os = "linux")]
fn clipboard_image(sender: tokio::sync::oneshot::Sender<Result<Option<String>, String>>) {
    use gtk::gdk;
    let clipboard = gtk::Clipboard::get(&gdk::SELECTION_CLIPBOARD);
    clipboard.request_contents(&gdk::Atom::intern("image/png"), move |clipboard, data| {
        if data.length() > MAX_BYTES as i32 {
            let _ = sender.send(Err("Choose an image smaller than 1.4 MB.".into()));
        } else if data.length() > 0 {
            let _ = sender.send(image_url(&data.data()).map(Some));
        } else {
            clipboard.request_contents(&gdk::Atom::intern("image/jpeg"), move |_, data| {
                let result = if data.length() > MAX_BYTES as i32 {
                    Err("Choose an image smaller than 1.4 MB.".into())
                } else if data.length() > 0 {
                    image_url(&data.data()).map(Some)
                } else {
                    Ok(None)
                };
                let _ = sender.send(result);
            });
        }
    });
}

#[tauri::command]
pub async fn read_clipboard_image(window: tauri::WebviewWindow) -> Result<Option<String>, String> {
    if window.label() != "main" {
        return Err("Image input is unavailable here.".into());
    }
    #[cfg(target_os = "linux")]
    {
        let (sender, receiver) = tokio::sync::oneshot::channel();
        // No polling or startup reads: this is called by an explicit paste only.
        window
            .run_on_main_thread(move || clipboard_image(sender))
            .map_err(|_| "The clipboard image could not be read.")?;
        tokio::time::timeout(Duration::from_secs(3), receiver)
            .await
            .map_err(|_| "The clipboard did not respond. Try pasting again.")?
            .map_err(|_| "The clipboard image could not be read.".to_string())?
    }
    #[cfg(not(target_os = "linux"))]
    {
        Ok(None)
    }
}


