#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod desktop;
mod job;
mod updates;
fn main() {
    let args: Vec<_> = std::env::args_os().collect();
    if args.get(1).is_some_and(|a| a == "--verify-update") {
        let valid = args.len() == 4
            && updates::verify_artifact(
                std::path::Path::new(&args[2]),
                std::path::Path::new(&args[3]),
            )
            .is_ok();
        std::process::exit(if valid { 0 } else { 1 });
    }
    desktop::run();
}
