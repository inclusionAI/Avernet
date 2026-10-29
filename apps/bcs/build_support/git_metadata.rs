//! Build-script Git metadata shared by the BCS server and CLI.

use std::path::Path;
use std::process::Command;

pub fn track_changes() {
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed={}", file!());
    if let Some(head) = git_path("HEAD") {
        track_existing_path(Path::new(&head));
    }
    if let Some(reference) = git_output(&["symbolic-ref", "--quiet", "HEAD"]) {
        if let Some(path) = git_path(&reference) {
            // Packed refs have no loose file yet. Watch the existing parent so
            // a new commit creating that file invalidates the embedded hash.
            track_existing_path(Path::new(&path));
        }
        if let Some(packed) = git_path("packed-refs") {
            if Path::new(&packed).is_file() {
                track_existing_path(Path::new(&packed));
            }
        }
    }
}

pub fn commit_hash() -> String {
    git_output(&["rev-parse", "--short", "HEAD"])
        .unwrap_or_else(|| "unknown".into())
}

fn git_path(name: &str) -> Option<String> {
    git_output(&["rev-parse", "--path-format=absolute", "--git-path", name])
}

fn git_output(args: &[&str]) -> Option<String> {
    let output = Command::new("git").args(args).output().ok()?;
    if !output.status.success() {
        return None;
    }
    let value = String::from_utf8(output.stdout).ok()?.trim().to_owned();
    if value.is_empty() { None } else { Some(value) }
}

fn track_existing_path(mut path: &Path) {
    // Cargo treats a missing rerun-if-changed path as permanently dirty.
    while !path.exists() {
        let Some(parent) = path.parent() else { return; };
        path = parent;
    }
    println!("cargo:rerun-if-changed={}", path.display());
}
