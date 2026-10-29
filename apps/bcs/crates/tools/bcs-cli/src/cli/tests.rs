use super::*;
use clap::{Parser, error::ErrorKind};
use serial_test::serial;
use std::io::Write;
use tempfile::TempDir;

#[path = "tests/arguments.rs"]
mod arguments;
#[path = "tests/environment.rs"]
mod environment;
#[path = "tests/group_context.rs"]
mod group_context;
#[path = "tests/collaboration.rs"]
mod collaboration;
#[path = "tests/channel.rs"]
mod channel;
#[path = "tests/session.rs"]
mod session;
#[path = "tests/service.rs"]
mod service;
#[path = "tests/output.rs"]
mod output;

// Helper to safely set env var
#[allow(unsafe_code)]
fn safe_set_var(key: &str, value: impl AsRef<std::ffi::OsStr>) {
    unsafe {
        std::env::set_var(key, value);
    }
}

// Helper to safely remove env var
#[allow(unsafe_code)]
fn safe_remove_var(key: &str) {
    unsafe {
        std::env::remove_var(key);
    }
}

fn write_session_file(temp_dir: &TempDir, value: serde_json::Value) {
    let session_dir = temp_dir.path().join(".bcs");
    std::fs::create_dir_all(&session_dir).unwrap();
    let session_file = session_dir.join("session.json");
    let mut file = std::fs::File::create(session_file).unwrap();
    file.write_all(serde_json::to_string_pretty(&value).unwrap().as_bytes())
        .unwrap();
}
