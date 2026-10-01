//! Smoke tests for bcs-cli
//!
//! Quick sanity checks that don't require mock server

use assert_cmd::Command;

/// Test that --help works and shows expected commands
#[test]
fn test_help_shows_expected_commands() {
    let mut cmd = Command::cargo_bin("bcs-cli").expect("Failed to find bcs-cli binary");
    cmd.arg("--help");
    
    // Verify key commands are documented (chain predicates)
    cmd.assert()
        .success()
        .stdout(predicates::str::contains("health"))
        .stdout(predicates::str::contains("list"))
        .stdout(predicates::str::contains("onboard"))
        .stdout(predicates::str::contains("chat"))
        .stdout(predicates::str::contains("group"));
}

/// Test that --version outputs something
#[test]
fn test_version_outputs() {
    let mut cmd = Command::cargo_bin("bcs-cli").expect("Failed to find bcs-cli binary");
    cmd.arg("--version");
    
    cmd.assert()
        .success()
        .stdout(predicates::str::contains("bcs-cli"));
}

/// Test that no args shows help
#[test]
fn test_no_args_shows_help() {
    let mut cmd = Command::cargo_bin("bcs-cli").expect("Failed to find bcs-cli binary");
    
    cmd.assert()
        .failure()
        .stderr(predicates::str::contains("Usage:"));
}
