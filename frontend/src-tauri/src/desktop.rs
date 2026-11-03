use std::{
    io::{Read, Write},
    net::TcpStream,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    time::{Duration, Instant},
};
use tauri::{
    menu::{MenuBuilder, MenuItemBuilder},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager, RunEvent, WindowEvent,
};
struct OwnedBackend {
    child: Child,
    _job: crate::job::Job,
}
struct BackendProcess(Mutex<Option<OwnedBackend>>);
#[derive(Clone, serde::Serialize)]
struct StartupStatus {
    phase: String,
    error: Option<String>,
}
struct StartupState(Mutex<StartupStatus>);
struct Stopping(AtomicBool);
struct Lifecycle(Mutex<()>);
fn log_dir() -> PathBuf {
    std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir)
        .join("LocalModelRouter")
}
fn backend_ready(instance: Option<&str>) -> bool {
    let Ok(mut stream) = TcpStream::connect_timeout(
        &"127.0.0.1:8787".parse().unwrap(),
        Duration::from_millis(200),
    ) else {
        return false;
    };
    let timeout = Some(Duration::from_millis(500));
    let _ = stream.set_read_timeout(timeout);
    let _ = stream.set_write_timeout(timeout);
    if stream
        .write_all(b"GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut response = String::new();
    if stream.take(8192).read_to_string(&mut response).is_err() {
        return false;
    }
    let Some((headers, body)) = response.split_once("\r\n\r\n") else {
        return false;
    };
    if !headers
        .lines()
        .next()
        .is_some_and(|line| line.contains(" 200 "))
    {
        return false;
    }
    let Ok(health) = serde_json::from_str::<serde_json::Value>(body) else {
        return false;
    };
    if health["status"] != "ok" {
        return false;
    }
    instance.is_none_or(|id| {
        health["application"] == "com.localmodelrouter.desktop"
            && health["version"] == env!("CARGO_PKG_VERSION")
            && health["api_version"] == 1
            && health["instance"] == id
    })
}
fn shutdown_backend(owned: &mut OwnedBackend) {
    if let Some(mut stdin) = owned.child.stdin.take() {
        let _ = stdin.write_all(b"stop\n");
    }
    let started = Instant::now();
    while started.elapsed() < Duration::from_secs(12) {
        if matches!(owned.child.try_wait(), Ok(Some(_))) {
            return;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    let _ = owned.child.kill();
    let _ = owned.child.wait();
}
fn start_backend(app: &tauri::AppHandle) -> Result<(), String> {
    if TcpStream::connect("127.0.0.1:8787").is_ok() {
        if cfg!(debug_assertions) && backend_ready(None) {
            return Ok(());
        }
        return Err("端口 8787 已被占用，请退出其他网关或开发版服务后重试。".into());
    }
    let dir = if cfg!(debug_assertions) {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../backend")
    } else {
        app.path()
            .resource_dir()
            .map_err(|e| e.to_string())?
            .join("backend")
    };
    let exe = if cfg!(debug_assertions) {
        dir.join(".venv/Scripts/python.exe")
    } else {
        dir.join("router-backend.exe")
    };
    if !exe.exists() {
        return Err(format!("缺少后端文件：{}，请重新安装。", exe.display()));
    }
    std::fs::create_dir_all(log_dir()).map_err(|e| e.to_string())?;
    let log_file = log_dir().join("desktop-backend.log");
    if std::fs::metadata(&log_file).is_ok_and(|m| m.len() > 5 * 1024 * 1024) {
        let _ = std::fs::remove_file(log_dir().join("desktop-backend.previous.log"));
        let _ = std::fs::rename(&log_file, log_dir().join("desktop-backend.previous.log"));
    }
    let log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_file)
        .map_err(|e| e.to_string())?;
    let instance = uuid::Uuid::new_v4().to_string();
    let mut command = Command::new(exe);
    if cfg!(debug_assertions) {
        command.arg(dir.join("desktop_entry.py"));
    }
    command
        .current_dir(dir)
        .env("PYTHONUNBUFFERED", "1")
        .env("LOCAL_MODEL_ROUTER_DESKTOP_INSTANCE", &instance)
        .stdin(Stdio::piped())
        .stdout(Stdio::from(log.try_clone().map_err(|e| e.to_string())?))
        .stderr(Stdio::from(log));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command
        .spawn()
        .map_err(|e| format!("无法启动本地服务：{e}"))?;
    let job = match crate::job::Job::attach(&child) {
        Ok(job) => job,
        Err(e) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(e.to_string());
        }
    };
    let mut owned = OwnedBackend { child, _job: job };
    let started = Instant::now();
    loop {
        if app.state::<Stopping>().0.load(Ordering::SeqCst) {
            shutdown_backend(&mut owned);
            return Err("应用正在退出".into());
        }
        if let Some(status) = owned.child.try_wait().map_err(|e| e.to_string())? {
            return Err(format!("本地服务启动失败（{status}），请查看日志。"));
        }
        if backend_ready(Some(&instance)) {
            break;
        }
        if started.elapsed() > Duration::from_secs(60) {
            shutdown_backend(&mut owned);
            return Err("本地服务启动超时，请重试或查看日志。".into());
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    *app.state::<BackendProcess>().0.lock().unwrap() = Some(owned);
    Ok(())
}
fn launch_backend(app: tauri::AppHandle) {
    std::thread::spawn(move || {
        let lifecycle = app.state::<Lifecycle>();
        let _guard = lifecycle.0.lock().unwrap();
        if app.state::<Stopping>().0.load(Ordering::SeqCst) {
            return;
        }
        let status = match start_backend(&app) {
            Ok(()) => StartupStatus {
                phase: "ready".into(),
                error: None,
            },
            Err(error) => StartupStatus {
                phase: "error".into(),
                error: Some(error),
            },
        };
        *app.state::<StartupState>().0.lock().unwrap() = status;
    });
}
fn stop_backend(handle: &tauri::AppHandle) {
    handle.state::<Stopping>().0.store(true, Ordering::SeqCst);
    let lifecycle = handle.state::<Lifecycle>();
    let _guard = lifecycle.0.lock().unwrap();
    if let Some(mut owned) = handle.state::<BackendProcess>().0.lock().unwrap().take() {
        shutdown_backend(&mut owned);
    }
}
#[tauri::command]
fn startup_status(app: tauri::AppHandle) -> StartupStatus {
    let state = app.state::<BackendProcess>();
    let mut backend = state.0.lock().unwrap();
    if let Some(owned) = backend.as_mut() {
        if let Ok(Some(code)) = owned.child.try_wait() {
            *backend = None;
            *app.state::<StartupState>().0.lock().unwrap() = StartupStatus {
                phase: "error".into(),
                error: Some(format!("本地服务已停止（{code}），请重试或查看日志。")),
            };
        }
    }
    app.state::<StartupState>().0.lock().unwrap().clone()
}
#[tauri::command]
fn retry_backend(app: tauri::AppHandle) {
    let state = app.state::<StartupState>();
    let mut status = state.0.lock().unwrap();
    if status.phase != "error" {
        return;
    }
    *status = StartupStatus {
        phase: "starting".into(),
        error: None,
    };
    drop(status);
    launch_backend(app);
}
#[tauri::command]
fn open_log_directory() -> Result<(), String> {
    std::fs::create_dir_all(log_dir()).map_err(|e| e.to_string())?;
    Command::new("explorer.exe")
        .arg(log_dir())
        .spawn()
        .map_err(|e| e.to_string())?;
    Ok(())
}
fn show_main_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}
pub fn run() {
    let mut context = tauri::generate_context!();
    if cfg!(debug_assertions) {
        context.config_mut().identifier.push_str(".dev");
    }
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            if args.iter().any(|a| a == "--quit") {
                app.exit(0);
            } else {
                show_main_window(app);
            }
        }))
        .manage(BackendProcess(Mutex::new(None)))
        .manage(Lifecycle(Mutex::new(())))
        .manage(Stopping(AtomicBool::new(false)))
        .manage(StartupState(Mutex::new(StartupStatus {
            phase: "starting".into(),
            error: None,
        })))
        .invoke_handler(tauri::generate_handler![
            startup_status,
            retry_backend,
            open_log_directory
        ])
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .setup(|app| {
            if std::env::args().any(|a| a == "--quit") {
                app.handle().exit(0);
                return Ok(());
            }
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
            launch_backend(app.handle().clone());
            Ok(())
        })
        .build(context)
        .expect("failed to build Local Model Router");
    app.run(|handle, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            stop_backend(handle);
        }
    });
}
