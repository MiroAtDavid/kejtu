//! Native folder selection returns a path only; the Files owner grants access on Save.
#[tauri::command]
pub async fn choose_folder(
    app: tauri::AppHandle,
    initial: Option<String>,
) -> Result<Option<String>, String> {
    if initial
        .as_ref()
        .is_some_and(|p| p.len() > 4096 || p.contains('\0'))
    {
        return Err("The folder location is invalid.".into());
    }
    #[cfg(target_os = "linux")]
    {
        use gtk::prelude::*;
        use tauri::Manager;
        let directory = crate::pdf_copy::save_directory(
            initial
                .as_deref()
                .map(std::path::Path::new)
                .and_then(|path| {
                    if path.is_absolute() {
                        path.ancestors().find(|p| p.is_dir())
                    } else {
                        None
                    }
                }),
            app.path().document_dir().ok().as_deref(),
            app.path().home_dir().ok().as_deref(),
        )
        .ok_or("No available folder. Check that your home folder is accessible.")?;
        let initial = Some(directory.to_string_lossy().into_owned());
        let (sender, receiver) = tokio::sync::oneshot::channel();
        let handle = app.clone();
        app.run_on_main_thread(move || {
            let parent = handle
                .get_webview_window("main")
                .and_then(|w| w.gtk_window().ok());
            open_dialog(parent.as_ref().map(|p| p.upcast_ref()), initial, sender);
        })
        .map_err(|_| "The folder chooser could not open.".to_string())?;
        receiver
            .await
            .map_err(|_| "The folder chooser closed unexpectedly.".to_string())?
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = (app, initial);
        Err("Folder selection is currently supported on Linux. Enter a path instead.".into())
    }
}

#[cfg(target_os = "linux")]
fn open_dialog(
    parent: Option<&gtk::Window>,
    initial: Option<String>,
    sender: tokio::sync::oneshot::Sender<Result<Option<String>, String>>,
) -> gtk::glib::WeakRef<gtk::FileChooserNative> {
    use gtk::prelude::*;
    use std::cell::RefCell;
    let dialog = gtk::FileChooserNative::new(
        Some("Choose a folder"),
        parent,
        gtk::FileChooserAction::SelectFolder,
        Some("Choose folder"),
        Some("Cancel"),
    );
    dialog.set_modal(true);
    dialog.set_create_folders(true);
    if let Some(path) = initial {
        let path = std::path::Path::new(&path);
        let existing = path.ancestors().find(|p| p.is_dir());
        if let Some(folder) = existing {
            dialog.set_current_folder(folder);
        }
    }
    let sender = RefCell::new(Some(sender));
    // GtkNativeDialog is a GObject, not a window-owned widget. Keep it
    // alive after this callback returns, then break the cycle on response.
    let retained = RefCell::new(Some(dialog.clone()));
    dialog.connect_response(move |dialog, response| {
        let result = if response == gtk::ResponseType::Accept {
            dialog
                .filename()
                .map(|path| {
                    path.into_os_string()
                        .into_string()
                        .map_err(|_| "Choose a folder with a UTF-8 name.".to_string())
                })
                .transpose()
        } else {
            Ok(None)
        };
        if let Some(sender) = sender.borrow_mut().take() {
            let _ = sender.send(result);
        }
        dialog.destroy();
        retained.borrow_mut().take();
    });
    dialog.show();
    dialog.downgrade()
}


