use base64::Engine;
use std::{sync::Arc, time::Duration};
use tauri::ipc::Channel;
use tauri_plugin_updater::{Update, UpdaterExt};
use tokio::sync::Mutex;

// Used by the build pipeline to verify the final artifact against the exact
// public key embedded in this desktop executable, before any feed is published.
pub(crate) fn verify_artifact(
    file: &std::path::Path,
    signature: &std::path::Path,
) -> Result<(), Box<dyn std::error::Error>> {
    let decode = |text: &str| -> Result<String, Box<dyn std::error::Error>> {
        Ok(String::from_utf8(
            base64::engine::general_purpose::STANDARD.decode(text.trim())?,
        )?)
    };
    let key =
        minisign_verify::PublicKey::decode(&decode(include_str!("../updater-public-key.txt"))?)?;
    let signature =
        minisign_verify::Signature::decode(&decode(&std::fs::read_to_string(signature)?)?)?;
    key.verify(&std::fs::read(file)?, &signature, true)?;
    Ok(())
}

#[derive(Default)]
struct UpdateSession {
    update: Option<Update>,
    // Keep only verified bytes in memory. No partially downloaded executable is
    // left behind or accepted through an arbitrary frontend path.
    bytes: Option<Vec<u8>>,
}

#[derive(Default, Clone)]
pub(crate) struct UpdateState(Arc<Mutex<UpdateSession>>);

#[derive(serde::Serialize)]
pub(crate) struct UpdateInfo {
    version: String,
    notes: Option<String>,
    date: Option<String>,
}

#[derive(Clone, serde::Serialize)]
pub(crate) struct DownloadProgress {
    downloaded: u64,
    total: Option<u64>,
}

fn endpoint(channel: &str) -> Result<String, String> {
    if !matches!(channel, "stable" | "preview") {
        return Err("未知的更新通道".into());
    }
    Ok(format!(
        "https://raw.githubusercontent.com/MangoFish163/AutoRoute/updates/{channel}.json"
    ))
}

fn check_window(window: &tauri::WebviewWindow) -> Result<(), String> {
    if window.label() != "main" {
        return Err("不允许的更新窗口".into());
    }
    Ok(())
}

#[tauri::command]
pub(crate) async fn check_app_update(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    state: tauri::State<'_, UpdateState>,
    channel: String,
) -> Result<Option<UpdateInfo>, String> {
    check_window(&window)?;
    let url = endpoint(&channel)?.parse().map_err(|_| "更新地址无效")?;
    let mut session = state.0.try_lock().map_err(|_| "另一个更新操作正在进行")?;
    *session = UpdateSession::default();
    let exit_handle = app.clone();
    let updater = app
        .updater_builder()
        .endpoints(vec![url])
        .map_err(|_| "更新地址无效")?
        .timeout(Duration::from_secs(20))
        // The plugin exits directly on Windows, bypassing RunEvent::Exit.
        // Stop our own backend explicitly before the installer replaces files.
        .on_before_exit(move || crate::desktop::stop_backend(&exit_handle))
        .build()
        .map_err(|_| "无法初始化更新检查")?;
    let update = updater
        .check()
        .await
        .map_err(|_| "无法获取更新信息，请检查网络，或稍后重试。该通道可能尚未发布版本。")?;
    if let Some(mut update) = update {
        if update.download_url.scheme() != "https" {
            return Err("更新包地址必须使用 HTTPS".into());
        }
        // A slow installer download gets a separate timeout from the small feed.
        update.timeout = Some(Duration::from_secs(15 * 60));
        let info = UpdateInfo {
            version: update.version.clone(),
            notes: update.body.clone(),
            date: update
                .raw_json
                .get("pub_date")
                .and_then(|v| v.as_str())
                .map(String::from),
        };
        session.update = Some(update);
        Ok(Some(info))
    } else {
        Ok(None)
    }
}

#[tauri::command]
pub(crate) async fn download_app_update(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, UpdateState>,
    on_event: Channel<DownloadProgress>,
) -> Result<(), String> {
    check_window(&window)?;
    let mut session = state.0.try_lock().map_err(|_| "另一个更新操作正在进行")?;
    session.bytes = None;
    let update = session.update.as_ref().ok_or("请先检查新版本")?;
    let mut downloaded = 0;
    let bytes = update
        .download(
            |length, total| {
                downloaded += length as u64;
                let _ = on_event.send(DownloadProgress { downloaded, total });
            },
            || {},
        )
        .await
        .map_err(|error| match error {
            tauri_plugin_updater::Error::Reqwest(_) | tauri_plugin_updater::Error::Network(_) => {
                "更新下载失败，请检查网络后重试。"
            }
            _ => "更新包验证失败，未进行安装。请重新检查更新或联系发布者。",
        })?;
    session.bytes = Some(bytes);
    Ok(())
}

#[tauri::command]
pub(crate) async fn install_app_update(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    state: tauri::State<'_, UpdateState>,
) -> Result<(), String> {
    check_window(&window)?;
    if cfg!(debug_assertions) {
        return Err("开发构建不安装更新，请使用正式打包的客户端。".into());
    }
    let shared = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let session = shared.0.try_lock().map_err(|_| "另一个更新操作正在进行")?;
        let update = session.update.as_ref().ok_or("请先检查新版本")?;
        let bytes = session.bytes.as_ref().ok_or("请先下载并验证更新包")?;
        if update.install(bytes).is_err() {
            // ShellExecute can fail after the pre-exit hook. Restore the gateway
            // and keep verified bytes available for a retry in this session.
            crate::desktop::recover_backend_after_update(&app);
            return Err("无法启动更新安装程序，本地服务正在恢复。请稍后重试安装。".into());
        }
        Ok(())
    })
    .await
    .map_err(|_| "更新安装任务异常，请重新打开应用后重试".to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn update_channels_cannot_supply_arbitrary_urls() {
        assert!(endpoint("preview")
            .unwrap()
            .ends_with("/updates/preview.json"));
        assert!(endpoint("stable")
            .unwrap()
            .ends_with("/updates/stable.json"));
        for invalid in ["", "../main", "https://example.com", "stable?url=other"] {
            assert!(endpoint(invalid).is_err());
        }
    }
}
