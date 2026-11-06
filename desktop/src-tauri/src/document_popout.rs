use tauri::{webview::NewWindowResponse, Manager, Url, WebviewUrl, WebviewWindowBuilder};

const PREFIX: &str = "document-chat-";

pub fn main_commands(
    invoke: tauri::ipc::Invoke<tauri::Wry>,
    commands: fn(tauri::ipc::Invoke<tauri::Wry>) -> bool,
) -> bool {
    // Controls mounted in the child call their original main-realm callbacks.
    // The child page itself must not dispatch native/backend operations.
    if invoke.message.webview_ref().label() != "main" {
        invoke
            .resolver
            .reject("Native operations belong to the main window");
        return true;
    }
    commands(invoke)
}
fn valid_token(token: &str) -> bool {
    token.len() == 32
        && token
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

#[tauri::command]
pub async fn position_document_chat(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    token: String,
    left: f64,
    top: f64,
    width: f64,
    height: f64,
) -> Result<(), String> {
    if window.label() != "main"
        || !valid_token(&token)
        || !valid_placement(left, top, width, height)
    {
        return Err("Invalid document chat placement".into());
    }
    let child = app
        .get_webview_window(&format!("{PREFIX}{token}"))
        .ok_or("The document chat window has closed")?;
    // WebKitGTK omits geometry in NewWindowFeatures. Apply the pointer's desktop
    // coordinates explicitly so a drag-out opens at the drop, including monitors
    // to the left or above the primary display.
    child
        .set_size(tauri::LogicalSize::new(width.max(360.0), height))
        .map_err(|e| e.to_string())?;
    child
        .set_position(tauri::LogicalPosition::new(left, top))
        .map_err(|e| e.to_string())?;
    child.show().map_err(|e| e.to_string())
}

fn valid_placement(left: f64, top: f64, width: f64, height: f64) -> bool {
    [left, top, width, height]
        .iter()
        .all(|n| n.is_finite() && n.abs() <= 100_000.0)
        && (320.0..=16_384.0).contains(&width)
        && (260.0..=16_384.0).contains(&height)
}

#[tauri::command]
pub async fn close_document_chat(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    token: String,
) -> Result<(), String> {
    if window.label() != "main" || !valid_token(&token) {
        return Err("Only the document editor can close its chat".into());
    }
    // WebKit's window.close() can close only the related WebView and leave an
    // empty GTK shell. The main owner destroys this exact transient window.
    if let Some(child) = app.get_webview_window(&format!("{PREFIX}{token}")) {
        child.destroy().map_err(|error| error.to_string())?;
    }
    Ok(())
}

fn chat_url(url: &Url, origin: &Url) -> bool {
    url.scheme() == origin.scheme()
        && url.host_str() == origin.host_str()
        && url.port_or_known_default() == origin.port_or_known_default()
        && url.username().is_empty()
        && url.password().is_none()
        && url.path() == "/document-chat.html"
        && url.query().is_none()
        && url.fragment().is_some_and(valid_token)
}

pub fn create_main(app: &mut tauri::App) -> tauri::Result<()> {
    let handle = app.handle().clone();
    let origin = if tauri::is_dev() {
        app.config().build.dev_url.clone().expect("development URL")
    } else {
        #[cfg(windows)]
        let origin = "http://tauri.localhost";
        #[cfg(not(windows))]
        let origin = "tauri://localhost";
        Url::parse(origin).expect("application origin")
    };
    let config = app
        .config()
        .app
        .windows
        .iter()
        .find(|w| w.label == "main")
        .expect("main window configuration");
    WebviewWindowBuilder::from_config(app, config)?
        .on_new_window(move |url, features| {
            if !chat_url(&url, &origin)
                || handle
                    .webview_windows()
                    .keys()
                    .filter(|label| label.starts_with(PREFIX))
                    .count()
                    >= 8
            {
                return NewWindowResponse::Deny;
            }
            let label = format!("{PREFIX}{}", url.fragment().unwrap());
            let allowed = url.clone();
            // Related WebViews preserve the opener and renderer realm. Only the
            // main editor mounts controls; this page has no application bootstrap.
            // about:blank is the builder placeholder; WebKit navigates to `url`.
            match WebviewWindowBuilder::new(
                &handle,
                label,
                WebviewUrl::External(Url::parse("about:blank").unwrap()),
            )
            .window_features(features)
            .title("Ask Kejtu")
            .decorations(true)
            .resizable(true)
            .inner_size(560.0, 360.0)
            .visible(false)
            .min_inner_size(360.0, 260.0)
            .on_navigation(move |next| next == &allowed || next.as_str() == "about:blank")
            .on_new_window(|_, _| NewWindowResponse::Deny)
            .build()
            {
                Ok(window) => NewWindowResponse::Create { window },
                Err(error) => {
                    eprintln!("Could not open document chat: {error}");
                    NewWindowResponse::Deny
                }
            }
        })
        .build()?;
    Ok(())
}

pub fn close_children(app: &tauri::AppHandle) {
    for (label, window) in app.webview_windows() {
        if label.starts_with(PREFIX) {
            let _ = window.destroy();
        }
    }
}


