use std::{collections::BTreeMap, fs, path::PathBuf};

use bcs_protocol::{
    CONTRACT_VERSION, MAGIC_KEY, TOOL_ASSIGN_TASK, TOOL_SEND_TASK_MESSAGE, TOOL_TASK_COMPLETE,
};
use serde::Deserialize;

#[derive(Debug, Deserialize)]
struct CoordinationContract {
    magic_key: String,
    version: u64,
    tools: BTreeMap<String, Vec<String>>,
}

fn load_contract() -> CoordinationContract {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("docs/contract.json");
    let body = fs::read_to_string(&path)
        .unwrap_or_else(|err| panic!("failed to read {}: {err}", path.display()));
    serde_json::from_str(&body).expect("contract.json should be valid JSON")
}

#[test]
fn contract_alignment_matches_rust_coordination_constants() {
    let contract = load_contract();

    assert_eq!(contract.magic_key, MAGIC_KEY);
    assert_eq!(contract.version, CONTRACT_VERSION);
    assert_eq!(
        contract.tools.keys().map(String::as_str).collect::<Vec<_>>(),
        vec![
            TOOL_ASSIGN_TASK,
            TOOL_SEND_TASK_MESSAGE,
            TOOL_TASK_COMPLETE,
        ]
    );
}

#[test]
fn contract_alignment_locks_tool_argument_shapes() {
    let contract = load_contract();

    assert_eq!(
        contract.tools.get(TOOL_ASSIGN_TASK).map(Vec::as_slice),
        Some(&["target_bot".to_string(), "message".to_string(), "response_mode?".to_string()][..])
    );
    assert_eq!(
        contract
            .tools
            .get(TOOL_SEND_TASK_MESSAGE)
            .map(Vec::as_slice),
        Some(&["message".to_string()][..])
    );
    assert_eq!(
        contract.tools.get(TOOL_TASK_COMPLETE).map(Vec::as_slice),
        Some(&["summary".to_string()][..])
    );
}

#[test]
fn reference_v2_is_additive_to_native_v1_contract() {
    let body = include_str!("../docs/contract.json");
    let value: serde_json::Value = serde_json::from_str(body).unwrap();
    assert_eq!(value["version"], 1);
    assert_eq!(value["echo_versions"], serde_json::json!([1, 2]));
    assert_eq!(value["reference_v2"]["status"], "stored");
    assert_eq!(value["reference_v2"]["arguments_in_echo"], false);
}
