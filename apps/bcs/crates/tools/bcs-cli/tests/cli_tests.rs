//! CLI --help and --version tests

use assert_cmd::Command;
use predicates::prelude::*;

#[test]
fn test_help_runs() {
    let mut cmd = Command::cargo_bin("bcs-cli").unwrap();
    cmd.arg("--help");
    cmd.assert()
        .success()
        .stdout(predicate::str::contains("Bot Coordination Service"));
}

#[test]
fn test_version_runs() {
    let mut cmd = Command::cargo_bin("bcs-cli").unwrap();
    cmd.arg("--version");
    cmd.assert().success();
}

#[test]
fn group_creation_help_exposes_only_direct_creation() {
    Command::cargo_bin("bcs-cli").unwrap()
        .arg("--help")
        .assert()
        .success()
        .stdout(predicate::str::contains("create-group"))
        .stdout(predicate::str::contains("request-group-help").not())
        .stdout(predicate::str::contains("confirm-group-help").not());

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
