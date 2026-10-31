use super::*;

    // The exact projection the trusted task edge emits for a delegated row
    // (review P1.III.2's input shape, byte-faithful). Injected into a
    // synthesized row in the test below.
    fn serialized_worker_projection() -> serde_json::Value {
        serde_json::json!({
            "version": 1,
            "caller_required": true,
            "caller_task": "GROUP TASK BODY",
            "caller_delegation": {
                "parent_run_id": "run-manager",
                "parent_attempt": 1,
                "parent_bot_id": "bot-manager",
                "invocation_id": "tool-call-1",
                "execution_purpose": "execution",
            },
            "driver_bot": "bot-driver", "originator": "bot-driver",
            "participants": [{"id": "bot-worker", "is_bot": true}],
            "direct_bot": false, "mentions": [], "target_tags": [], "sender_name": "mgr",
            "text_projection": "original",
            "task": {"leg": "dispatch", "task_id": "t1", "manager": "bot-driver",
                     "worker": "bot-worker", "worker_name": "Worker",
                     "response_mode": "full", "summary": ""},
        })
    }


    fn worker_row() -> PersistedMessageDelivery {
        PersistedMessageDelivery {
            delivery_id: "d".into(),
            env: "local".into(),
            source_message_id: "m".into(),
            target_bot_id: "bot-worker".into(),
            session_id: "s".into(),
            group_id: "g".into(),
            source_session_seq: 0,
            flow_kind: DeliveryFlowKind::Task,
            state: bcs_domain::message_delivery::MessageDeliveryState {
                kind: DeliveryType::Send,
                status: bcs_domain::message_delivery::MessageDeliveryStatus::Queued,
                state_version: 1,
                may_have_been_sent: false,
            },
            wait_reason: None,
            available_at_ms: 0,
            expire_at_ms: None,
            created_at_ms: 1,
            updated_at_ms: 1,
            run_id: Some("run-w".into()),
            idempotency_key: None,
            attempt_no: 1,
            request_id: None,
            send_started_at_ms: None,
            submitted_at_ms: None,
            accepted_at_ms: None,
            run_deadline_at_ms: None,
            terminal_at_ms: None,
            bound_to_delivery_id: None,
            cancel_requested_at_ms: None,
            cancel_requested_by: None,
            cancel_reason: None,
            abort_request_id: None,
            abort_started_at_ms: None,
            cancel_deadline_at_ms: None,
            last_error_code: None,
            semantic_projection_json: serde_json::Value::Null,
            transport_context_json: None,
            context_selection_json: None,
        }
    }

    /// The runtime-stage caller facts EXACTLY as the managed-delivery CAS
    /// transitions write them: CommitCallerDispatch (StartSend), the typed
    /// terminal arbitration and the cancel intent — plus the task edge's
    /// trusted sender channel facts.
    fn dispatch_committed_worker_projection() -> serde_json::Value {
        let mut projection = serialized_worker_projection();
        projection["caller_channel"] = serde_json::json!({
            "actor_id": "bot-driver", "actor_name": "Driver",
        });
        projection["caller_dispatch"] = serde_json::json!({
            "version": 1i64,
            "caller_request_digest": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "caller_not_after_ms": 300_000i64,
            "caller_control_revision": 0u64,
        });
        projection["caller_terminal"] = serde_json::json!({
            "version": 1i64,
            "outcome": "completed",
            "event_seq": 7u64,
            "caller_control_revision": 0u64,
        });
        projection["caller_cancel"] = serde_json::json!({
            "version": 1i64,
            "cancel_request_id": "cancel-1",
            "caller_control_revision": 0u64,
            "reason": "user",
            "not_after_ms": 300_000i64,
        });
        projection
    }

    #[test]
    fn runtime_stage_facts_decode_and_never_travel_to_result_or_notice_projections() {
        // A row that has been through the REAL protected dispatch carries
        // the CAS-parked runtime facts; the strict decode must accept it
        // (deny_unknown_fields otherwise rejects the row as "invalid queued
        // group projection"), and every caller fact must be stripped from
        // the Manager-facing result and timeout-notice projections.
        let mut row = worker_row();
        row.semantic_projection_json = dispatch_committed_worker_projection();
        row.semantic_projection_json["task"]["leg"] = serde_json::json!("result");
        let task = crate::queued_task::TaskIntent {
            timeout_s: None,
            leg: crate::queued_task::TaskLeg::Result,
            task_id: "t1".into(), manager: "bot-driver".into(), worker: "bot-worker".into(),
            worker_name: "Worker".into(),
            response_mode: bcs_service_api::ChatResponseMode::Full,
            assignment_intent_id: None, summary: String::new(),
        };
        let projection = QueuedGroupProjection::task_result(&row, task, Vec::new())
            .expect("a dispatch-committed row decodes for the result leg");
        let out = serde_json::to_value(&projection).unwrap();
        for key in [
            "caller_channel", "caller_dispatch", "caller_terminal", "caller_cancel",
            "caller_execution", "caller_required", "caller_task", "caller_delegation",
            "caller_lineage",
        ] {
            assert!(out.get(key).is_none(), "the result projection leaks {key}");
        }
        // The timeout notice rides the same ordinary lifecycle: a timed-out
        // caller dispatched row must not hand the Manager a caller-required
        // (forever un-preparable) system notice.
        let notice = QueuedGroupProjection::timeout_notice(&row, "bot-driver")
            .expect("a dispatch-committed row decodes for the timeout notice");
        let notice_out = serde_json::to_value(&notice).unwrap();
        for key in [
            "caller_channel", "caller_dispatch", "caller_terminal", "caller_cancel",
            "caller_execution", "caller_required", "caller_task", "caller_delegation",
            "caller_lineage",
        ] {
            assert!(notice_out.get(key).is_none(), "the timeout notice leaks {key}");
        }
    }

    #[test]
    fn task_result_never_carries_the_worker_dispatch_caller_facts() {
        // The result delivery answers the dispatch — it is a DIFFERENT
        // execution: the Manager must see the WORKER'S RESULT (carried by
        // the reply's own canonical message), never the original task
        // text, and never the Worker's config-decided execution purpose.
        let mut row = worker_row();
        row.semantic_projection_json = serialized_worker_projection();
        row.semantic_projection_json["task"]["leg"] = serde_json::json!("result");
        // §7.2.1: even if a lineage marker ever landed on this row, the
        // result leg is never an association carrier.
        row.semantic_projection_json["caller_lineage"] = serde_json::json!("delegation_only");
        let task = crate::queued_task::TaskIntent {
            timeout_s: None,
            leg: crate::queued_task::TaskLeg::Result,
            task_id: "t1".into(), manager: "bot-driver".into(), worker: "bot-worker".into(),
            worker_name: "Worker".into(),
            response_mode: bcs_service_api::ChatResponseMode::Full,
            assignment_intent_id: None, summary: String::new(),
        };
        let projection = QueuedGroupProjection::task_result(&row, task, Vec::new()).unwrap();
        let out = serde_json::to_value(&projection).unwrap();
        assert!(
            out.get("caller_required").is_none(),
            "the result projection carries no caller_required mark"
        );
        assert!(
            out.get("caller_task").is_none(),
            "the result projection never re-sends the dispatch task text"
        );
        assert!(
            out.get("caller_delegation").is_none(),
            "the worker's delegated execution purpose never leaks toward the Manager"
        );
        assert!(
            out.get("caller_lineage").is_none(),
            "the result projection is never a delegation-only lineage carrier"
        );
        // The dispatch row itself stays UNTOUCHED (its parse-shell just
        // for the result build): re-decode the row and the marks are all
        // still there — the fix is scoped to the RESULT projection.
        let again: QueuedGroupProjection = serde_json::from_value(row.semantic_projection_json).unwrap();
        let again_out = serde_json::to_value(&again).unwrap();
        assert_eq!(again_out["caller_required"], true);
        assert_eq!(again_out["caller_task"], "GROUP TASK BODY");
        assert!(again_out.get("caller_delegation").is_some());
        assert_eq!(again_out["caller_lineage"], "delegation_only");
    }
