mod backend;
mod backend_process;
mod files;
mod image_input;
mod package_startup;
mod pdf_copy;
mod private_file;
mod transport;
mod web_links;
mod wire;

use backend::Backend;
use serde_json::Value;
use tauri::Manager;
use tokio::sync::Mutex;
type Session = Mutex<Result<Backend, String>>;

#[cfg(desktop)]
fn window_state_flags() -> tauri_plugin_window_state::StateFlags {
    // Frame style belongs to configuration. Visibility belongs to first render,
    // never a saved session: restoring it early flashes an unpainted WebView.
    tauri_plugin_window_state::StateFlags::all().difference(
        tauri_plugin_window_state::StateFlags::DECORATIONS
            | tauri_plugin_window_state::StateFlags::VISIBLE,
    )
}

#[cfg(desktop)]
fn persist_window_changes(app: &tauri::App) {
    use std::sync::{
        atomic::{AtomicU64, Ordering},
        Arc,
    };
    use tauri_plugin_window_state::AppHandleExt;

    let Some(window) = app.get_webview_window("main") else {
        return;
    };
    let handle = app.handle().clone();
    let revision = Arc::new(AtomicU64::new(0));
    window.on_window_event(move |event| {
        if !matches!(
            event,
            tauri::WindowEvent::Resized(_) | tauri::WindowEvent::Moved(_)
        ) {
            return;
        }
        let current = revision.fetch_add(1, Ordering::Relaxed) + 1;
        let revision = revision.clone();
        let app = handle.clone();
        tauri::async_runtime::spawn(async move {
            // The plugin normally writes only at Exit. Managed SIGTERM stops
            // bypass that hook, so persist once a resize/move has settled too.
            tokio::time::sleep(std::time::Duration::from_millis(250)).await;
            if revision.load(Ordering::Relaxed) != current {
                return;
            }
            let writer = app.clone();
            // Window queries and the plugin cache share the UI event thread;
            // querying while holding its cache on a worker can deadlock resize.
            if let Err(error) = app.run_on_main_thread(move || {
                if revision.load(Ordering::Relaxed) == current {
                    if let Err(error) = writer.save_window_state(window_state_flags()) {
                        eprintln!("Could not save window state: {error}");
                    }
                }
            }) {
                eprintln!("Could not schedule window state save: {error}");
            }
        });
    });
}

fn admission_open() -> Result<(), String> {
    #[cfg(windows)]
    if backend_process::desktop_stopping() {
        return Err("native session is closing".into());
    }
    Ok(())
}

#[tauri::command]
async fn native_request(state: tauri::State<'_, Session>, request: Value) -> Result<Value, String> {
    admission_open()?;
    let mut session = state.lock().await;
    admission_open()?;
    session
        .as_mut()
        .map_err(|error| error.clone())?
        .request(request)
        .await
}

#[tauri::command]
async fn native_pending(state: tauri::State<'_, Session>) -> Result<Value, String> {
    admission_open()?;
    let mut session = state.lock().await;
    admission_open()?;
    session
        .as_mut()
        .map_err(|error| error.clone())?
        .reconcile_pending()
        .await
}

#[tauri::command]
async fn native_rendered(state: tauri::State<'_, Session>, evidence: Value) -> Result<(), String> {
    admission_open()?;
    let session = state.lock().await;
    admission_open()?;
    session
        .as_ref()
        .map_err(|error| error.clone())?
        .record_rendered(evidence)
}

#[tauri::command]
async fn native_present(window: tauri::WebviewWindow, marker: String) -> Result<(), String> {
    // Presentation is independent of backend readiness: startup errors must
    // become visible too, without manufacturing a successful readiness receipt.
    if marker != backend::RENDERED_MARKER {
        return Err("The application surface has not rendered.".into());
    }
    let (sender, received) = tokio::sync::oneshot::channel();
    let surface_window = window.clone();
    // GTK objects belong to the UI thread; commands may run on the async pool.
    window
        .run_on_main_thread(move || {
            let result = (|| {
                #[cfg(target_os = "linux")]
                {
                    use gtk::prelude::WidgetExt;
                    if let Some(surface) = surface_window
                        .gtk_window()
                        .map_err(|error| error.to_string())?
                        .window()
                    {
                        surface.set_opacity(1.0);
                    }
                }
                surface_window.show().map_err(|error| error.to_string())
            })();
            let _ = sender.send(result);
        })
        .map_err(|error| error.to_string())?;
    received.await.map_err(|error| error.to_string())?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default();
    #[cfg(desktop)]
    let builder = builder.plugin(
        tauri_plugin_window_state::Builder::default()
            .with_state_flags(window_state_flags())
            .build(),
    );

    builder
        .setup(|app| {
            // WebKitGTK needs a mapped window to compute its viewport and paint.
            // Map transparently: the first visible frame is the themed app (or
            // its error screen), not a small uninitialized native surface.
            #[cfg(target_os = "linux")]
            if let Some(window) = app.get_webview_window("main") {
                use gtk::prelude::WidgetExt;
                let native = window.gtk_window()?;
                native.realize();
                if let Some(surface) = native.window() {
                    surface.set_opacity(0.0);
                }
                window.show()?;
            }
            image_input::connect(app);
            #[cfg(desktop)]
            persist_window_changes(app);
            let backend = tauri::async_runtime::block_on(Backend::start());
            #[cfg(windows)]
            backend_process::install_exit(backend.as_ref().ok().map(Backend::exit_owner))?;
            app.manage(Mutex::new(backend));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            native_request,
            native_pending,
            native_rendered,
            native_present,
            files::choose_folder,
            pdf_copy::save_pdf_copy,
            pdf_copy::save_docx_copy,
            pdf_copy::save_markdown_copy,
            image_input::read_dropped_image,
            image_input::read_clipboard_image,
            web_links::open_web_link
        ])
        .build(tauri::generate_context!())
        .expect("Kejtu Next development runtime failed")
        .run(|app, event| {
            #[cfg(windows)]
            if let Some(exit) = backend_process::desktop_exit() {
                match &event {
                    tauri::RunEvent::ExitRequested { code, api, .. } => {
                        if *code == Some(tauri::RESTART_EXIT_CODE) {
                            // Tauri cannot prevent restart. It has no orderly
                            // close claim; still remove this exact owned tree.
                            eprintln!("Native backend restart requires forced cleanup");
                            exit.emergency();
                        } else if !exit.finished() {
                            api.prevent_exit();
                            if exit.begin() {
                                let app = app.clone();
                                let code = code.unwrap_or(0);
                                std::thread::spawn(move || {
                                    exit.finish();
                                    app.exit(code);
                                });
                            }
                        }
                    }
                    tauri::RunEvent::Exit => exit.emergency(),
                    _ => {}
                }
            }
            #[cfg(unix)]
            if matches!(
                event,
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
            ) {
                // Preserve the existing Unix signal/Drop path.
                let state = app.state::<Session>();
                let owner = tauri::async_runtime::block_on(async {
                    std::mem::replace(
                        &mut *state.lock().await,
                        Err("native session closed".into()),
                    )
                });
                drop(owner);
            }
        });
}
