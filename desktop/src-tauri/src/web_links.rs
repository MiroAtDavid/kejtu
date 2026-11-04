//! User-initiated links only; model markup cannot navigate the application.
fn destination(value: &str) -> Result<&str, String> {
    if value.len() > 8192
        || value
            .chars()
            .any(|c| c <= ' ' || c == '\u{7f}' || c == '\\')
    {
        return Err("Unsupported web address.".into());
    }
    let url = reqwest::Url::parse(value).map_err(|_| "Unsupported web address.")?;
    if !matches!(url.scheme(), "http" | "https")
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || !(value.to_ascii_lowercase().starts_with("https://")
            || value.to_ascii_lowercase().starts_with("http://"))
    {
        return Err("Unsupported web address.".into());
    }
    let authority = value.split_once("://").ok_or("Unsupported web address.")?.1;
    if authority.starts_with('/') || authority.is_empty() {
        return Err("Unsupported web address.".into());
    }
    Ok(value)
}

#[tauri::command]
pub async fn open_web_link(url: String) -> Result<(), String> {
    destination(&url)?;
    // An argument, never a shell command. This does not hold the backend lock.
    tauri::async_runtime::spawn_blocking(move || {
        #[cfg(target_os = "linux")]
        let status = std::process::Command::new("xdg-open").arg(&url).status();
        #[cfg(target_os = "macos")]
        let status = std::process::Command::new("open").arg(&url).status();
        #[cfg(target_os = "windows")]
        let status = std::process::Command::new("rundll32.exe")
            .args(["url.dll,FileProtocolHandler", &url])
            .status();
        match status {
            Ok(value) if value.success() => Ok(()),
            _ => Err("Could not open this web address.".into()),
        }
    })
    .await
    .map_err(|_| "Could not open this web address.".to_string())?
}


