//! Smoke tests for bcs-cli
//!
//! Quick sanity checks that don't require mock server

use assert_cmd::Command;
use predicates::prelude::*;

/// Test that --help works and shows expected commands
#[test]
fn test_help_shows_expected_commands() {
    let mut cmd = Command::cargo_bin("bcs-cli").expect("Failed to find bcs-cli binary");
    cmd.arg("--help");
    
    // Verify key commands are documented (chain predicates)
    cmd.assert()
        .success()
        .stdout(predicate::str::contains("health"))
        .stdout(predicate::str::contains("list"))
        .stdout(predicate::str::contains("onboard"))
        .stdout(predicate::str::contains("chat"))
        .stdout(predicate::str::contains("Bot Coordination Service"))
        .stdout(predicate::str::contains("create-group"))
        .stdout(predicate::str::contains("request-group-help").not())
        .stdout(predicate::str::contains("confirm-group-help").not());
}

/// Test that --version outputs something
#[test]
fn test_version_outputs() {
    let mut cmd = Command::cargo_bin("bcs-cli").expect("Failed to find bcs-cli binary");
    cmd.arg("--version");
    
    cmd.assert()
        .success()
        .stdout(predicate::str::contains("bcs-cli"));
}

/// Test that no args shows help
#[test]
fn test_no_args_shows_help() {
    let mut cmd = Command::cargo_bin("bcs-cli").expect("Failed to find bcs-cli binary");
    
    cmd.assert()
        .failure()
        .stderr(predicate::str::contains("Usage:"));
}

#[test]
fn group_creation_help_exposes_only_direct_creation() {
    Command::cargo_bin("bcs-cli").unwrap()
        .args(["create-group", "--help"])
        .assert()
        .success()
        .stdout(predicate::str::contains("--driver"))
        .stdout(predicate::str::contains("--manager"))
        .stdout(predicate::str::contains("--participants"))
        .stdout(predicate::str::contains("--no-session"));
}

#[test]
fn proposal_commands_are_rejected_by_argument_parser() {
    for command in ["request-group-help", "confirm-group-help"] {
        Command::cargo_bin("bcs-cli").unwrap()
            .args([command, "--help"])
            .assert()
            .code(2)
            .stderr(predicate::str::contains("unrecognized subcommand"));
    }
}
