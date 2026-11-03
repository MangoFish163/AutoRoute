#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod desktop;
mod job;
fn main() {
    desktop::run();
}
