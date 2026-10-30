#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    net::TcpStream,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
};
use tauri::{
    menu::{MenuBuilder, MenuItemBuilder},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager, RunEvent, WindowEvent,
};

struct BackendProcess(Mutex<Option<Child>>);

fn backend_paths(app: &tauri::App) -> Result<(PathBuf, PathBuf), String> {
    if cfg!(debug_assertions) {
        let backend_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../backend");
        let python = backend_dir.join(".venv/Scripts/python.exe");
        return Ok((backend_dir, python));
    }

    let resource_dir = app.path().resource_dir().map_err(|e| e.to_string())?;
    let backend_dir = resource_dir.join("backend");
    let python = std::env::var_os("LOCAL_MODEL_ROUTER_PYTHON")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("python.exe"));
    Ok((backend_dir, python))
}

fn start_backend(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    // Reuse the backend if the browser launcher already started it.
    if TcpStream::connect("127.0.0.1:8787").is_ok() {
        app.manage(BackendProcess(Mutex::new(None)));
        return Ok(());
    }

    let (backend_dir, python) = backend_paths(app).map_err(std::io::Error::other)?;
    if !backend_dir.join("app.py").exists() {
        return Err(format!("Backend not found at {}", backend_dir.display()).into());
    }
    if cfg!(debug_assertions) && !python.exists() {
        return Err(format!(
            "Backend Python environment not found at {}. Run start.ps1 once to prepare it.",
            python.display()
        )
        .into());
    }

    let log_dir = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir)
        .join("LocalModelRouter");
    std::fs::create_dir_all(&log_dir)?;
    let log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_dir.join("desktop-backend.log"))?;
    let child = Command::new(python)
        .args(["-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8787"])
        .current_dir(backend_dir)
        .stdin(Stdio::null())
        .stdout(Stdio::from(log.try_clone()?))
        .stderr(Stdio::from(log))
        .spawn()?;
    app.manage(BackendProcess(Mutex::new(Some(child))));
    Ok(())
}

fn stop_backend(handle: &tauri::AppHandle) {
    if let Some(state) = handle.try_state::<BackendProcess>() {
        if let Ok(mut child) = state.0.lock() {
            if let Some(mut process) = child.take() {
                #[cfg(windows)]
                {
                    use std::os::windows::process::CommandExt;
                    let pid = process.id().to_string();
                    // The Windows Python launcher can create a runtime child process;
                    // terminate the owned process tree so uvicorn cannot be orphaned.
                    let _ = Command::new("taskkill")
                        .args(["/PID", &pid, "/T", "/F"])
                        .creation_flags(0x08000000) // CREATE_NO_WINDOW
                        .output();
                }
                let _ = process.kill();
                let _ = process.wait();
            }
        }
    }
}

fn show_main_window(handle: &tauri::AppHandle) {
    if let Some(window) = handle.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn main() {
    let app = tauri::Builder::default()
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                // Closing the window keeps the local gateway available in the tray.
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .setup(|app| {
            start_backend(app)?;

            let show = MenuItemBuilder::with_id("show", "打开主窗口").build(app)?;
            let quit = MenuItemBuilder::with_id("quit", "退出应用").build(app)?;
            let menu = MenuBuilder::new(app).items(&[&show, &quit]).build()?;
            let mut tray = TrayIconBuilder::new()
                .tooltip("Local Model Router：点击打开，右键可退出")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "show" => show_main_window(app),
                    "quit" => app.exit(0),
                    _ => (),
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_main_window(tray.app_handle());
                    }
                });
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build Local Model Router");

    app.run(|handle, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            stop_backend(handle);
        }
    });
}
