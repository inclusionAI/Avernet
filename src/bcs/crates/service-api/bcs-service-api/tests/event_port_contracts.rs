use async_trait::async_trait;
use bcs_service_api::port::{
    EventDeliveryAttemptMetric, EventDeliveryDisposition, EventDeliveryError, EventDeliveryPort,
    EventDeliveryRequest, EventDeliveryResponse, EventErrorCategory, EventProductionMetric,
    EventRecordError, EventRecordResult, EventRecorderPort, EventRepoPort,
    EventingInstrumentationPort, NewEvent, WebhookGuardBlockReason,
};

#[test]
fn event_ports_are_transport_neutral_and_object_safe() {
    fn accepts(
        _: Option<&dyn EventRecorderPort>,
        _: Option<&dyn EventDeliveryPort>,
        _: Option<&dyn EventingInstrumentationPort>,
        _: Option<&dyn EventRepoPort>,
    ) {
    }

    accepts(None, None, None, None);
}

struct ContractRecorder;

#[async_trait]
impl EventRecorderPort for ContractRecorder {
    async fn record(&self, event: NewEvent) -> Result<EventRecordResult, EventRecordError> {
        Ok(EventRecordResult::Recorded {
            event_id: event.event_id,
            stream_sequence: 1,
            fanout_target_count: 0,
        })
    }
}

struct ContractDelivery;

#[async_trait]
impl EventDeliveryPort for ContractDelivery {
    async fn deliver(
        &self,
        _request: EventDeliveryRequest,
    ) -> Result<EventDeliveryResponse, EventDeliveryError> {
        Ok(EventDeliveryResponse {
            disposition: EventDeliveryDisposition::Succeeded,
            http_status: Some(204),
            retry_after_ms: None,
            response_bytes_observed: 0,
            error_category: None,
            error_summary: None,
        })
    }
}

struct ContractMetrics;

#[async_trait]
impl EventingInstrumentationPort for ContractMetrics {
    async fn event_produced(&self, _metric: EventProductionMetric) {}

    async fn fanout_failed(&self, _error_category: EventErrorCategory) {}

    async fn delivery_attempted(&self, _metric: EventDeliveryAttemptMetric) {}

    async fn webhook_guard_blocked(&self, _reason: WebhookGuardBlockReason) {}
}

#[tokio::test]
async fn centralized_event_port_harnesses_are_executable() {
    use std::collections::BTreeMap;

    use bcs_service_api::application::v1::{EventScope, EventSubject};
    use bcs_test_support::contract::port::{
        event_delivery_port_contract_tests, event_recorder_port_contract_tests,
        eventing_instrumentation_port_contract_tests,
    };

    event_recorder_port_contract_tests(
        &ContractRecorder,
        NewEvent {
            event_id: "evt-contract".into(),
            event_type: "group.created".into(),
            schema_version: "1.0".into(),
            producer: "contract".into(),
            producer_key: "group-1".into(),
            occurred_at: "2026-08-19T00:00:00.000Z".into(),
            subject: EventSubject {
                subject_type: "group".into(),
                id: "group-1".into(),
            },
            scope: EventScope {
                group_id: Some("group-1".into()),
                ..EventScope::default()
            },
            stream_key: "group:group-1".into(),
            actor: None,
            correlation_id: None,
            causation_event_id: None,
            trace_id: None,
            data: BTreeMap::new(),
        },
    )
    .await;

    let delivery_request = EventDeliveryRequest {
        endpoint_url: "https://example.com/events".into(),
        body: br#"{"event_id":"evt-contract","data":{"content":"private"}}"#.to_vec(),
        request_timeout_ms: 10_000,
    };
    let request_debug = format!("{delivery_request:?}");
    assert!(!request_debug.contains("example.com"));
    assert!(!request_debug.contains("private"));

    event_delivery_port_contract_tests(
        &ContractDelivery,
        delivery_request,
        EventDeliveryDisposition::Succeeded,
    )
    .await;

    eventing_instrumentation_port_contract_tests(&ContractMetrics).await;
}
