use bcs_config_api::message_delivery::DeliveryPolicy;
use bcs_message_flow::delivery_policy::LiveDeliveryPolicy;
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::{HumanActor, CallerContext};
use std::sync::Arc;

#[derive(Clone)]
struct LogWriter(Arc<std::sync::Mutex<Vec<u8>>>);
impl std::io::Write for LogWriter {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> { self.0.lock().unwrap().extend_from_slice(bytes); Ok(bytes.len()) }
    fn flush(&mut self) -> std::io::Result<()> { Ok(()) }
}

#[tokio::test]
async fn policy_audit_records_human_versions_fields_and_both_outcomes() {
    let bytes = Arc::new(std::sync::Mutex::new(Vec::new()));
    let writer = LogWriter(bytes.clone());
    let subscriber = tracing_subscriber::fmt().with_max_level(tracing::Level::TRACE).json().with_writer(move || writer.clone()).finish();
    // Dedicated test binary installs its subscriber before any policy callsites run.
    tracing::subscriber::set_global_default(subscriber).unwrap();
    async {
        let caller = || CallerContext::Human(HumanActor { actor_id: "human_audit_operator".into(), staff_no: "audit_operator".into() });
        let live = LiveDeliveryPolicy::new(Arc::new(MemoryMessageRepo::new()), Default::default(), false);
        let mut policy = DeliveryPolicy::default();
        policy.defaults.max_queued = 50;
        live.replace(caller(), 0, policy.clone()).await.unwrap();
        assert!(live.replace(caller(), 0, policy).await.is_err());
    }.await;
    let output = String::from_utf8(bytes.lock().unwrap().clone()).unwrap();
    let events: Vec<serde_json::Value> = output.lines().map(|line| serde_json::from_str(line).unwrap()).collect();
    let audit: Vec<_> = events.iter().filter(|value| value["fields"]["message"] == "delivery policy update audited" && value["fields"]["actor_id"] == "human_audit_operator").collect();
    assert_eq!(audit.len(), 2, "captured audit: {output}");
    assert_eq!(audit[0]["fields"]["actor_id"], "human_audit_operator");
    assert_eq!(audit[0]["fields"]["before_version"], 0);
    assert_eq!(audit[0]["fields"]["after_version"], 1);
    assert_eq!(audit[0]["fields"]["changed_fields"], "defaults.max_queued");
    assert_eq!(audit[0]["fields"]["outcome"], "success");
    assert_eq!(audit[1]["fields"]["outcome"], "failure");
    assert_eq!(audit[1]["fields"]["before_version"], 1);
    assert_eq!(audit[1]["fields"]["after_version"], 1);
    assert!(audit[0]["fields"]["attempted_at_ms"].as_i64().unwrap() > 0);
    assert!(!output.contains("\"policy\":"));
}
