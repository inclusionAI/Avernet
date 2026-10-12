use super::*;

#[test]
fn generated_slug_survives_retry_and_changes_only_after_completion() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bridge.toml");
    let first = pending::prepare(&path, "same-request", None).unwrap();
    assert_eq!(first, pending::prepare(&path, "same-request", None).unwrap());
    pending::submitted(&path).unwrap();
    assert!(pending::prepare(&path, "different-request", None).is_err());
    pending::complete(&path).unwrap();
    assert_ne!(first, pending::prepare(&path, "same-request", None).unwrap());
}

#[test]
fn only_subbot_configs_can_append() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bridge.toml");
    std::fs::write(&path, "provider_id='p'\nprovider_auth='agentpass-subbots'\nmode='plugin'\nbot=[]\n").unwrap();
    assert!(config::read_for_registration(&path, "agentpass-subbots", "p", "plugin").is_ok());
    assert!(config::read_for_registration(&path, "agentpass", "p", "plugin").is_err());
    assert!(config::read_for_registration(&path, "agentpass-subbots", "other", "plugin").is_err());
    assert!(config::read_for_registration(&path, "agentpass-subbots", "p", "gateway").is_err());
}

#[test]
fn pre_post_attempt_can_be_replaced_but_submitted_attempt_cannot() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bridge.toml");
    pending::prepare(&path, "request-a", Some("a")).unwrap();
    assert_eq!(pending::prepare(&path, "request-b", Some("b")).unwrap(), "b");
    pending::submitted(&path).unwrap();
    assert!(pending::prepare(&path, "request-c", Some("c")).is_err());
}
