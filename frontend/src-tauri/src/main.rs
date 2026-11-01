#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    io::{Read, Write},
    net::TcpStream,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::{Duration, Instant},
};
use tauri::{
    menu::{MenuBuilder, MenuItemBuilder},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager, RunEvent, WindowEvent,
};

struct BackendProcess(Mutex<Option<Child>>);

fn backend_ready(address: &str) -> bool {
    let Ok(address) = address.parse() else { return false };
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(200)) else {
        return false;
    };
    let timeout = Some(Duration::from_millis(500));
    if stream.set_read_timeout(timeout).is_err() || stream.set_write_timeout(timeout).is_err() {
        return false;
    }
    if stream.write_all(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n").is_err() {
        return false;
    }
    let mut response = String::new();
    if stream.take(8192).read_to_string(&mut response).is_err() {
        return false;
    }
    let Some((headers, body)) = response.split_once("\r\n\r\n") else { return false };
    if !headers.lines().next().is_some_and(|line| line.starts_with("HTTP/1.1 200 ") || line.starts_with("HTTP/1.0 200 ")) {
        return false;
    }
    let Ok(health) = serde_json::from_str::<serde_json::Value>(body) else { return false };
    health["status"] == "ok" && health["version"].is_string() && health["usage_schema"].is_number()
}

fn wait_for_backend(address: &str, mut child: Option<&mut Child>, timeout: Duration) -> std::io::Result<()> {
    let started = Instant::now();
    loop {
        if let Some(child) = child.as_deref_mut() {
            if let Some(status) = child.try_wait()? {
                return Err(std::io::Error::other(format!("Backend exited during startup ({status})")));
            }
        }
        if backend_ready(address) {
            return Ok(());
        }
        if started.elapsed() >= timeout {
            return Err(std::io::Error::new(std::io::ErrorKind::TimedOut, "Backend health check timed out"));
        }
        std::thread::sleep(Duration::from_millis(200));
    }
}

fn terminate_backend(process: &mut Child) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let _ = Command::new("taskkill")
            .args(["/PID", &process.id().to_string(), "/T", "/F"])
            .creation_flags(0x08000000) // CREATE_NO_WINDOW
            .output();
    }
    let _ = process.kill();
    let _ = process.wait();
}

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
        eprintln!("Waiting for the existing backend at http://127.0.0.1:8787/health ...");
        wait_for_backend("127.0.0.1:8787", None, Duration::from_secs(60))
            .map_err(|error| std::io::Error::other(format!("{error}. Port 8787 is occupied but Local Model Router is not ready.")))?;
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
    let mut command = Command::new(python);
    command.args(["-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8787"])
        .current_dir(backend_dir)
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::from(log.try_clone()?))
        .stderr(Stdio::from(log));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    let mut child = command.spawn()?;
    eprintln!("Waiting for backend startup at http://127.0.0.1:8787/health ...");
    if let Err(error) = wait_for_backend("127.0.0.1:8787", Some(&mut child), Duration::from_secs(60)) {
        // Clean up only the process this window started; never kill a reused service.
        if !matches!(child.try_wait(), Ok(Some(_))) {
            terminate_backend(&mut child);
        }
        return Err(std::io::Error::other(format!("{error}. Backend log: {}", log_dir.join("desktop-backend.log").display())).into());
    }
    eprintln!("Backend ready at http://127.0.0.1:8787");
    app.manage(BackendProcess(Mutex::new(Some(child))));
    Ok(())
}

fn stop_backend(handle: &tauri::AppHandle) {
    if let Some(state) = handle.try_state::<BackendProcess>() {
        if let Ok(mut child) = state.0.lock() {
            if let Some(mut process) = child.take() {
                terminate_backend(&mut process);
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
            show_main_window(app.handle());
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
