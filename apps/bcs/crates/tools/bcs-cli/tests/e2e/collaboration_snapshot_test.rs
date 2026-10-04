use assert_cmd::Command;

use crate::common::{assert_failure, assert_success};

const DOCUMENT_ID: &str = "11111111-1111-4111-8111-111111111111";
const SNAPSHOT_ID: &str = "22222222-2222-4222-8222-222222222222";

fn command_without_bcs_configuration() -> Command {
    let mut command = Command::cargo_bin("bcs-cli").unwrap();
    command
        .env_remove("BCS_API_BASE_URL")
        .env_remove("MOLTIS_BCS_URL")
        .env_remove("BCN_BOT_TOKEN")
        .env_remove("BOT_DATA_DIR")
        .env_remove("BCS_COOKIE")
        .env("NO_COLOR", "1");
    command
}

#[test]
fn preview_plain_output_is_deterministic_and_offline() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("definition.yaml");
    let yaml = "name: 示例\r\nnote: a  b & <tag> '</AixUI>' 😀\n";
    std::fs::write(&file, yaml).unwrap();

    let output = command_without_bcs_configuration()
        .args([
            "--no-json",
            "collaboration",
            "preview",
        ])
        .arg(&file)
        .args([
            "--title",
            "示例 流程",
            "--document-id",
            DOCUMENT_ID,
            "--snapshot-id",
            SNAPSHOT_ID,
        ])
        .output()
        .unwrap();

    assert_success(&output);
    assert!(output.stderr.is_empty(), "{:?}", output.stderr);
    let message = String::from_utf8_lossy(&output.stdout);
    assert!(message.starts_with(
        "<AixUI type=\"card\" component=\"bcsPanel.CollaborationDefinitionCard\" params='"
    ));
    assert!(message.contains("\\u0026"));
    assert!(message.contains("\\u003c/AixUI\\u003e"));
    assert!(!message.contains("http://127.0.0.1:21000"));

    let replay = command_without_bcs_configuration()
        .args(["--no-json", "collaboration", "preview"])
        .arg(&file)
        .args([
            "--title",
            "示例 流程",
            "--document-id",
            DOCUMENT_ID,
            "--snapshot-id",
            SNAPSHOT_ID,
        ])
        .output()
        .unwrap();
    assert_eq!(output.stdout, replay.stdout);

    let default_output = command_without_bcs_configuration()
        .args(["collaboration", "preview"])
        .arg(&file)
        .args([
            "--title",
            "示例 流程",
            "--document-id",
            DOCUMENT_ID,
            "--snapshot-id",
            SNAPSHOT_ID,
        ])
        .output()
        .unwrap();
    assert_success(&default_output);
    assert_eq!(output.stdout, default_output.stdout);
}

#[test]
fn preview_structured_output_contains_snapshot_and_identical_message() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("invalid-definition.yaml");
    std::fs::write(&file, "this is not valid collaboration YAML").unwrap();

    let output = command_without_bcs_configuration()
        .args(["--json", "collaboration", "preview"])
        .arg(&file)
        .args([
            "--document-id",
            DOCUMENT_ID,
            "--snapshot-id",
            SNAPSHOT_ID,
        ])
        .output()
        .unwrap();
    assert_success(&output);

    let json: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(json["snapshot"]["schemaVersion"], 1);
    assert_eq!(json["snapshot"]["definitionYaml"], "this is not valid collaboration YAML");
    assert_eq!(json["snapshot"]["documentId"], DOCUMENT_ID);
    assert_eq!(json["snapshot"]["snapshotId"], SNAPSHOT_ID);
    assert!(json["message"]
        .as_str()
        .unwrap()
        .contains("bcsPanel.CollaborationDefinitionCard"));
}

#[test]
fn preview_accepts_canonical_uuid_v7_snapshot_identity() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("definition.yaml");
    std::fs::write(&file, "name: uuid-v7\n").unwrap();

    let output = command_without_bcs_configuration()
        .args(["--json", "collaboration", "preview"])
        .arg(&file)
        .args([
            "--document-id",
            "0191a59c-7e00-7000-8000-000000000001",
            "--snapshot-id",
            "0191a59c-7e00-7000-8000-000000000002",
            "--parent-snapshot-id",
            "0191a59c-7e00-7000-8000-000000000003",
        ])
        .output()
        .unwrap();

    assert_success(&output);
    let json: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(
        json["snapshot"]["documentId"],
        "0191a59c-7e00-7000-8000-000000000001"
    );
    assert_eq!(
        json["snapshot"]["snapshotId"],
        "0191a59c-7e00-7000-8000-000000000002"
    );
}

#[test]
fn preview_rejects_invalid_inputs_with_empty_stdout() {
    let dir = tempfile::tempdir().unwrap();
    let valid = dir.path().join("valid.yaml");
    std::fs::write(&valid, "name: example\n").unwrap();
    let invalid_utf8 = dir.path().join("invalid-utf8.yaml");
    std::fs::write(&invalid_utf8, [0xff, 0xfe]).unwrap();
    let too_long_title = "a".repeat(121);

    let invalid_cases = [
        (&invalid_utf8, vec![]),
        (&valid, vec!["--parent-snapshot-id", SNAPSHOT_ID]),
        (&valid, vec!["--document-id", "not-a-uuid"]),
        (&valid, vec!["--title", "   "]),
        (&valid, vec!["--title", too_long_title.as_str()]),
        (
            &valid,
            vec![
                "--document-id",
                DOCUMENT_ID,
                "--snapshot-id",
                SNAPSHOT_ID,
                "--parent-snapshot-id",
                SNAPSHOT_ID,
            ],
        ),
    ];
    for (file, args) in invalid_cases {
        let output = command_without_bcs_configuration()
            .args(["collaboration", "preview"])
            .arg(file)
            .args(args)
            .output()
            .unwrap();
        assert_failure(&output, Some(1));
        assert!(output.stdout.is_empty());
    }

    let missing = command_without_bcs_configuration()
        .args(["collaboration", "preview", "does-not-exist.yaml"])
        .output()
        .unwrap();
    assert_failure(&missing, Some(1));
    assert!(missing.stdout.is_empty());
}

#[test]
fn preview_enforces_yaml_and_serialized_message_byte_limits() {
    let dir = tempfile::tempdir().unwrap();
    let exact_yaml = dir.path().join("exact.yaml");
    std::fs::write(&exact_yaml, "a".repeat(65_536)).unwrap();
    let accepted = command_without_bcs_configuration()
        .args(["collaboration", "preview"])
        .arg(&exact_yaml)
        .args(["--document-id", DOCUMENT_ID, "--snapshot-id", SNAPSHOT_ID])
        .output()
        .unwrap();
    assert_success(&accepted);

    let oversized_yaml = dir.path().join("oversized.yaml");
    std::fs::write(&oversized_yaml, "a".repeat(65_537)).unwrap();
    let rejected = command_without_bcs_configuration()
        .args(["collaboration", "preview"])
        .arg(&oversized_yaml)
        .output()
        .unwrap();
    assert_failure(&rejected, Some(1));
    assert!(rejected.stdout.is_empty());

    let escaped_yaml = dir.path().join("escaped.yaml");
    std::fs::write(&escaped_yaml, " ".repeat(65_536)).unwrap();
    let escaped_rejected = command_without_bcs_configuration()
        .args(["collaboration", "preview"])
        .arg(&escaped_yaml)
        .output()
        .unwrap();
    assert_failure(&escaped_rejected, Some(1));
    assert!(escaped_rejected.stdout.is_empty());
    assert!(String::from_utf8_lossy(&escaped_rejected.stderr).contains("262144 byte limit"));
}
