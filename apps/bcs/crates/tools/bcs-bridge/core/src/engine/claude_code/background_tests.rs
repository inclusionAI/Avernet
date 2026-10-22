use super::*;
    async fn run_mock_cc(script: &str) -> Result<TurnOutcome, TurnError> {
        let engine = ClaudeCode::new(PathBuf::from("bash"), vec![script.to_string()]);
        let req = TurnRequest::new("r-bg", "hi", ".", Arc::new(NullObserver));
        let (tx, _rx) = tokio::sync::mpsc::channel::<StreamEvent>(64);
        engine
            .run_turn(req, tx, tokio_util::sync::CancellationToken::new())
            .await
    }

    #[test]
    fn task_lifecycle_lines_map_to_tracking_variants() {
        // Shapes recorded from a live CLI capture. task_started carries the
        // ids the mapper registers by; heavy task_notification system lines
        // are INERT (enqueue-time only — release happens on delivery);
        // delivered notifications arrive as user lines with plain-string
        // `<task-notification>` content.
        let started = r#"{"type":"system","subtype":"task_started","task_id":"t1","tool_use_id":"toolu_1","task_type":"local_agent"}"#;
        match map_cc_line(started, "r-1") {
            CcMap::BackgroundStarted { task_id, tool_use_id } => {
                assert_eq!(task_id.as_deref(), Some("t1"));
                assert_eq!(tool_use_id, "toolu_1");
            }
            other => panic!("expected BackgroundStarted, got {other:?}"),
        }
        // Enqueue-time terminal lines record the queued notification — a slot
        // in the wake queue, never a release on their own (the CLI defers
        // delivery while the model is mid-round; production 2026-10-14:
        // results cut the turn with two enqueued-but-undelivered finders).
        let enq = r#"{"type":"system","subtype":"task_notification","task_id":"t1","tool_use_id":"toolu_1","status":"completed","summary":"done"}"#;
        match map_cc_line(enq, "r-1") {
            CcMap::BackgroundEnqueued { task_id, tool_use_id, status } => {
                assert_eq!(task_id.as_deref(), Some("t1"));
                assert_eq!(tool_use_id.as_deref(), Some("toolu_1"));
                assert_eq!(status, "completed");
            }
            other => panic!("expected BackgroundEnqueued, got {other:?}"),
        }
        // A terminal notice without ids still occupies a future wake, so it
        // enqueues too; wake-side resolution handles the missing ids.
        let anonymous = r#"{"type":"system","subtype":"task_notification","status":"completed"}"#;
        match map_cc_line(anonymous, "r-1") {
            CcMap::BackgroundEnqueued { task_id, tool_use_id, .. } => {
                assert_eq!(task_id, None);
                assert_eq!(tool_use_id, None);
            }
            other => panic!("expected anonymous BackgroundEnqueued, got {other:?}"),
        }
        // Non-terminal statuses and id-less task_started stay inert.
        for inert in [
            r#"{"type":"system","subtype":"task_notification","task_id":"t1","tool_use_id":"toolu_1","status":"running"}"#,
            r#"{"type":"system","subtype":"task_started","task_id":"t2"}"#,
        ] {
            assert!(matches!(map_cc_line(inert, "r-1"), CcMap::Ignore), "must be inert: {inert}");
        }
        // The delivered notification: user line, plain-string content,
        // fields extracted for the delivery-driven release.
        let delivered = json!({
            "type": "user",
            "message": {"role": "user", "content":
                "<task-notification>\n<task-id>t1</task-id>\n<tool-use-id>toolu_1</tool-use-id>\n<status>completed</status>\n<summary>done</summary>\n</task-notification>"}
        }).to_string();
        match map_cc_line(&delivered, "r-1") {
            CcMap::BackgroundDelivered { task_id, tool_use_id, status } => {
                assert_eq!(task_id.as_deref(), Some("t1"));
                assert_eq!(tool_use_id.as_deref(), Some("toolu_1"));
                assert_eq!(status, "completed");
            }
            other => panic!("expected BackgroundDelivered, got {other:?}"),
        }
        // Legacy delivery without <tool-use-id>: task_id rides for correlation.
        let legacy = json!({
            "type": "user",
            "message": {"role": "user", "content":
                "<task-notification>\n<task-id>t1</task-id>\n<status>completed</status>\n</task-notification>"}
        }).to_string();
        match map_cc_line(&legacy, "r-1") {
            CcMap::BackgroundDelivered { task_id, tool_use_id, status } => {
                assert_eq!(task_id.as_deref(), Some("t1"));
                assert_eq!(tool_use_id, None);
                assert_eq!(status, "completed");
            }
            other => panic!("expected BackgroundDelivered, got {other:?}"),
        }
        // A delivered payload without <status> cannot release (the release
        // rule is terminal-status-driven): reads as not-a-notification.
        let statusless = json!({
            "type": "user",
            "message": {"role": "user", "content":
                "<task-notification>\n<task-id>t1</task-id>\n<tool-use-id>toolu_1</tool-use-id>\n</task-notification>"}
        }).to_string();
        assert!(matches!(map_cc_line(&statusless, "r-1"), CcMap::Ignore));
    }

    #[test]
    fn enqueue_side_notification_does_not_release_only_delivery_does() {
        // The wait releases on the delivered user `<task-notification>`, never
        // on the enqueue-time system line — and plain-string content only,
        // so tool results echoing notification text (a CR reading this very
        // source) can never impersonate a delivery.
        let mut mapper = CcTurnMapper::new("r-1");
        let started = r#"{"type":"system","subtype":"task_started","task_id":"t-bg","tool_use_id":"toolu_bg","task_type":"local_agent"}"#;
        assert!(matches!(mapper.map_line(started), CcMap::Ignore));
        assert_eq!(mapper.background_pending(), 1);
        let enqueued = r#"{"type":"system","subtype":"task_notification","task_id":"t-bg","tool_use_id":"toolu_bg","status":"completed"}"#;
        assert!(matches!(mapper.map_line(enqueued), CcMap::Ignore));
        assert_eq!(mapper.background_pending(), 1, "enqueue must keep the wait");
        let delivered = json!({
            "type": "user",
            "message": {"role": "user", "content":
                "<task-notification>\n<task-id>t-bg</task-id>\n<tool-use-id>toolu_bg</tool-use-id>\n<status>completed</status>\n</task-notification>"}
        }).to_string();
        assert!(matches!(mapper.map_line(&delivered), CcMap::Ignore));
        assert_eq!(mapper.background_pending(), 0, "delivery releases");
    }

    #[test]
    fn notification_text_in_tool_results_does_not_read_as_delivery() {
        // Foreground tool output that carries the notification payload — a CR
        // reading this fixture, a grep over an tasks/ directory — rides in
        // block arrays; only plain-string user content is a delivery.
        let mut mapper = CcTurnMapper::new("r-1");
        let started = r#"{"type":"system","subtype":"task_started","task_id":"t-bg","tool_use_id":"toolu_e","task_type":"local_agent"}"#;
        assert!(matches!(mapper.map_line(started), CcMap::Ignore));
        let read_start = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_r","name":"Read","input":{"path":"mock fixture"}}]}}"#;
        assert!(matches!(mapper.map_line(read_start), CcMap::Events(_)));
        let echo = json!({
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_r", "is_error": false, "content": [
                {"type": "text", "text": "<task-notification>\n<task-id>t-bg</task-id>\n<tool-use-id>toolu_e</tool-use-id>\n<status>completed</status>\n</task-notification>"}
            ]}]}
        }).to_string();
        assert!(matches!(mapper.map_line(&echo), CcMap::Events(_)), "tool_result array must map as events");
        assert_eq!(mapper.background_pending(), 1, "tool output must not release the wait");
    }

    #[test]
    fn inline_tool_result_for_a_backgrounded_id_releases_the_wait() {
        // Claude Code 2.1.199 auto-backgrounds a long-running foreground Bash
        // (no run_in_background from the model): it emits task_started, then an
        // enqueue-time task_notification, then delivers the real output as an
        // ORDINARY block-array tool_result carrying the SAME tool_use_id — and
        // never surfaces a `<task-notification>` user line nor a `system/init`
        // wake. Recorded live (repro6): sleep 25 → task_started(toolu),
        // task_notification(completed), user tool_result(id=toolu), result.
        // The delivery IS that tool_result, so a result for a registered
        // background id must release the wait — otherwise the run's `result`
        // is swallowed forever and the turn hangs until the idle watchdog.
        let mut mapper = CcTurnMapper::new("r-1");
        let start = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_bg","name":"Bash","input":{"command":"sleep 25; echo DONE","timeout":120000}}]}}"#;
        assert!(matches!(mapper.map_line(start), CcMap::Events(_)));
        let started = r#"{"type":"system","subtype":"task_started","task_id":"t-bg","tool_use_id":"toolu_bg","task_type":"local_bash"}"#;
        assert!(matches!(mapper.map_line(started), CcMap::Ignore));
        assert_eq!(mapper.background_pending(), 1, "task_started registers the wait");
        let enqueued = r#"{"type":"system","subtype":"task_notification","task_id":"t-bg","tool_use_id":"toolu_bg","status":"completed"}"#;
        assert!(matches!(mapper.map_line(enqueued), CcMap::Ignore));
        assert_eq!(mapper.background_pending(), 1, "enqueue alone must not release");
        // The real output, delivered inline as an ordinary tool_result with the
        // backgrounded id. This is the delivery this engine actually surfaces.
        let delivered = json!({
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_bg", "is_error": false, "content": [
                {"type": "text", "text": "DONE"}
            ]}]}
        }).to_string();
        assert!(matches!(mapper.map_line(&delivered), CcMap::Events(_)), "still forwarded as a tool event");
        assert_eq!(mapper.background_pending(), 0, "inline delivery of a backgrounded id releases the wait");
    }

    #[test]
    fn inline_delivery_cancels_its_wake_queue_alias() {
        // The enqueue-time task_notification parked an alias in the wake queue.
        // Once the inline tool_result releases the wait directly, that alias is
        // spent: a later system/init wake must not pop it and drain a DIFFERENT
        // task's still-pending wait.
        let mut mapper = CcTurnMapper::new("r-1");
        for (t, u) in [("t-x", "toolu_x"), ("t-y", "toolu_y")] {
            let started = format!(
                r#"{{"type":"system","subtype":"task_started","task_id":"{t}","tool_use_id":"{u}","task_type":"local_bash"}}"#
            );
            assert!(matches!(mapper.map_line(&started), CcMap::Ignore));
            let noticed = format!(
                r#"{{"type":"system","subtype":"task_notification","task_id":"{t}","tool_use_id":"{u}","status":"completed"}}"#
            );
            assert!(matches!(mapper.map_line(&noticed), CcMap::Ignore));
        }
        assert_eq!(mapper.background_pending(), 2);
        // x delivered inline (ordinary tool_result) — releases x and cancels
        // its queued alias.
        let delivered_x = json!({
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_x", "is_error": false, "content": "x done"}]}
        }).to_string();
        assert!(matches!(mapper.map_line(&delivered_x), CcMap::Events(_)));
        assert_eq!(mapper.background_pending(), 1, "x released by its inline delivery");
        // A wake must release y — impossible if x's spent alias still fronts it.
        let wake = r#"{"type":"system","subtype":"init","session_id":"cc-sess-wake"}"#;
        assert!(matches!(mapper.map_line(wake), CcMap::SessionId(_)));
        assert_eq!(mapper.background_pending(), 0, "wake releases y, not x's spent alias");
    }

    #[test]
    fn inline_delivery_cancels_the_matching_alias_not_a_taskidless_one() {
        // Two auto-backgrounded tasks. Task A's task_notification OMITS task_id
        // (some CLI recordings do), so its queued alias is {task_id: None, …}.
        // When B is delivered inline, cancelling B's alias must key on B's own
        // id — it must NOT match A's notice merely because A's task_id is also
        // absent. Otherwise the following wake pops B's spent slot, A's wait is
        // stranded, and the turn hangs until the idle watchdog.
        let mut mapper = CcTurnMapper::new("r-1");
        for u in ["toolu_a", "toolu_b"] {
            let started = format!(
                r#"{{"type":"system","subtype":"task_started","tool_use_id":"{u}","task_type":"local_bash"}}"#
            );
            assert!(matches!(mapper.map_line(&started), CcMap::Ignore));
        }
        assert_eq!(mapper.background_pending(), 2);
        // A's completion notification omits task_id; B's carries both keys.
        let notice_a = r#"{"type":"system","subtype":"task_notification","tool_use_id":"toolu_a","status":"completed"}"#;
        assert!(matches!(mapper.map_line(notice_a), CcMap::Ignore));
        let notice_b = r#"{"type":"system","subtype":"task_notification","task_id":"t-b","tool_use_id":"toolu_b","status":"completed"}"#;
        assert!(matches!(mapper.map_line(notice_b), CcMap::Ignore));
        assert_eq!(mapper.background_pending(), 2, "enqueue alone must not release");
        // B delivered inline — releases B and must cancel B's alias, not A's.
        let delivered_b = json!({
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_b", "is_error": false, "content": "b done"}]}
        }).to_string();
        assert!(matches!(mapper.map_line(&delivered_b), CcMap::Events(_)));
        assert_eq!(mapper.background_pending(), 1, "B released by its inline delivery");
        // The wake must release A through A's still-queued alias. If B's cancel
        // wrongly removed A's alias, the wake pops B's spent slot and A hangs.
        let wake = r#"{"type":"system","subtype":"init","session_id":"cc-sess-wake"}"#;
        assert!(matches!(mapper.map_line(wake), CcMap::SessionId(_)));
        assert_eq!(mapper.background_pending(), 0, "wake releases A, not B's spent alias");
    }

    #[test]
    fn background_launch_requires_evidence_not_just_the_request() {
        // A `run_in_background: true` request alone is not a launch: a denied or
        // invalid call never emits task_started or a receipt, and registering
        // it would wedge the turn on work that will never notify.
        let mut mapper = CcTurnMapper::new("r-1");
        let start = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_d","name":"Bash","input":{"command":"sleep 1","run_in_background":true}}]}}"#;
        assert!(matches!(mapper.map_line(start), CcMap::Events(_)));
        assert_eq!(mapper.background_pending(), 0, "request alone must not register");
        // The authoritative launch evidence does.
        let launched = r#"{"type":"system","subtype":"task_started","task_id":"t1","tool_use_id":"toolu_d","task_type":"local_bash"}"#;
        assert!(matches!(mapper.map_line(launched), CcMap::Ignore));
        assert_eq!(mapper.background_pending(), 1);
    }

    #[test]
    fn receipt_registration_requires_initiating_tool_and_whole_text_prefix() {
        // Receipt markers seen inside other tools' output — CRs reading this
        // very source, grep over logs — must not read as background launches.
        let mut mapper = CcTurnMapper::new("r-1");
        let read_start = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_r","name":"Read","input":{"path":"claude_code.rs"}}]}}"#;
        assert!(matches!(mapper.map_line(read_start), CcMap::Events(_)));
        let read_result = r#"{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_r","content":"core/src/engine/claude_code.rs: text.contains(\"Async agent launched successfully\")"}]}}"#;
        assert!(matches!(mapper.map_line(read_result), CcMap::Events(_)));
        assert_eq!(mapper.background_pending(), 0, "substring in a Read result must not register");
        // A Bash result that merely mentions the sentence mid-text: excluded.
        let bash_start = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_g","name":"Bash","input":{"command":"grep receipt src"}}]}}"#;
        assert!(matches!(mapper.map_line(bash_start), CcMap::Events(_)));
        let grep_result = r#"{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_g","content":"logs: text.contains(\"Command running in background\")"}]}}"#;
        assert!(matches!(mapper.map_line(grep_result), CcMap::Events(_)));
        assert_eq!(mapper.background_pending(), 0, "mid-text mention must not register");
        // The recorded receipt shape — the whole result, from an initiating
        // tool — registers.
        let bg_start = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_b","name":"Bash","input":{"command":"sleep 1","run_in_background":true}}]}}"#;
        assert!(matches!(mapper.map_line(bg_start), CcMap::Events(_)));
        let receipt = r#"{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_b","content":"Command running in background with ID: bp1. Output is being written to: /tmp/x. You will be notified when it completes.","is_error":false}]}}"#;
        assert!(matches!(mapper.map_line(receipt), CcMap::Events(_)));
        assert_eq!(mapper.background_pending(), 1);
    }

    #[test]
    fn task_id_correlation_releases_waits_from_deliveries_without_tool_id() {
        let mut mapper = CcTurnMapper::new("r-1");
        let started = r#"{"type":"system","subtype":"task_started","task_id":"t-legacy","tool_use_id":"toolu_leg","task_type":"local_agent"}"#;
        assert!(matches!(mapper.map_line(started), CcMap::Ignore));
        assert_eq!(mapper.background_pending(), 1);
        // Recorded pre-0.3.269 shape: the delivered notification omits
        // <tool-use-id>; task_id must release the correlated wait.
        let legacy_delivered = json!({
            "type": "user",
            "message": {"role": "user", "content":
                "<task-notification>\n<task-id>t-legacy</task-id>\n<status>completed</status>\n</task-notification>"}
        }).to_string();
        assert!(matches!(mapper.map_line(&legacy_delivered), CcMap::Ignore));
        assert_eq!(mapper.background_pending(), 0, "task_id must release the correlated wait");
    }

    #[test]
    fn wake_inits_release_one_enqueued_notification_each() {
        // Real-capture protocol (2026-10-14 production + lab): this CLI
        // never surfaces the delivered <task-notification> on stdout —
        // completions arrive as enqueue-time system lines, and each
        // auto-wake round (a new system/init) hands the model exactly ONE
        // queued notification. Draining one wait per wake is the ONLY way
        // this pending set can ever empty, so it is the only way the turn
        // can ever finalize.
        let mut mapper = CcTurnMapper::new("r-1");
        for (t, u) in [("t-a", "toolu_a"), ("t-b", "toolu_b"), ("t-c", "toolu_c")] {
            let started = format!(
                r#"{{"type":"system","subtype":"task_started","task_id":"{t}","tool_use_id":"{u}","task_type":"local_agent"}}"#
            );
            assert!(matches!(mapper.map_line(&started), CcMap::Ignore));
        }
        assert_eq!(mapper.background_pending(), 3);
        for (t, u) in [("t-a", "toolu_a"), ("t-b", "toolu_b"), ("t-c", "toolu_c")] {
            let noticed = format!(
                r#"{{"type":"system","subtype":"task_notification","task_id":"{t}","tool_use_id":"{u}","status":"completed"}}"#
            );
            assert!(matches!(mapper.map_line(&noticed), CcMap::Ignore));
            assert_eq!(mapper.background_pending(), 3, "enqueue must keep the wait");
        }
        // Three wakes deliver the three queued notifications — one each. C
        // was enqueued before B's wake yet delivered a round later, so the
        // pops must not race ahead of arrivals: one release per wake, no
        // more, whoever fronts the queue.
        for expected in [2, 1, 0] {
            let wake = r#"{"type":"system","subtype":"init","session_id":"cc-sess-wake"}"#;
            assert!(matches!(mapper.map_line(wake), CcMap::SessionId(_)));
            assert_eq!(mapper.background_pending(), expected, "one wake releases exactly one");
        }
        // A fourth wake (round running with nothing left queued) must be a
        // no-op — it can neither release a wait that does not exist nor
        // fabricate a stale-marker event.
        let wake = r#"{"type":"system","subtype":"init","session_id":"cc-sess-wake"}"#;
        assert!(matches!(mapper.map_line(wake), CcMap::SessionId(_)));
        assert_eq!(mapper.background_pending(), 0);
    }

    #[test]
    fn delivered_user_line_cancels_its_alias_in_the_wake_queue() {
        // Engines that DO surface deliveries (user <task-notification> lines)
        // resolve their wait directly — and must cancel the queued alias from
        // the enqueue-time system line, or the following wake pops the spent
        // alias instead, and the notification that wake actually delivered is
        // left owning a wait that stays pending forever.
        let mut mapper = CcTurnMapper::new("r-1");
        for (t, u) in [("t-x", "toolu_x"), ("t-y", "toolu_y")] {
            let started = format!(
                r#"{{"type":"system","subtype":"task_started","task_id":"{t}","tool_use_id":"{u}","task_type":"local_agent"}}"#
            );
            assert!(matches!(mapper.map_line(&started), CcMap::Ignore));
        }
        assert_eq!(mapper.background_pending(), 2);
        for (t, u) in [("t-x", "toolu_x"), ("t-y", "toolu_y")] {
            let noticed = format!(
                r#"{{"type":"system","subtype":"task_notification","task_id":"{t}","tool_use_id":"{u}","status":"completed"}}"#
            );
            assert!(matches!(mapper.map_line(&noticed), CcMap::Ignore));
            assert_eq!(mapper.background_pending(), 2, "enqueue must keep the wait");
        }
        // x is delivered as a user line (CLI versions that surface the
        // delivery on stdout): releases x directly, cancels its alias.
        let delivered = json!({
            "type": "user",
            "message": {"role": "user", "content":
                "<task-notification>\n<task-id>t-x</task-id>\n<tool-use-id>toolu_x</tool-use-id>\n<status>completed</status>\n</task-notification>"}
        }).to_string();
        assert!(matches!(mapper.map_line(&delivered), CcMap::Ignore));
        assert_eq!(mapper.background_pending(), 1, "user line releases its own wait");
        // The wake that delivers y must release y — impossible while x's
        // spent alias still fronts the queue.
        let wake = r#"{"type":"system","subtype":"init","session_id":"cc-sess-wake"}"#;
        assert!(matches!(mapper.map_line(wake), CcMap::SessionId(_)));
        assert_eq!(
            mapper.background_pending(), 0,
            "wake must release y, not pop x's spent alias"
        );
    }

    #[test]
    fn stale_delivery_for_unknown_task_emits_marked_event() {
        // A notification whose tool call id was never registered this run
        // (a replay from a previous killed turn) must not be silent: forward
        // it downstream with a visible marker.
        let mut mapper = CcTurnMapper::new("r-1");
        let stale = json!({
            "type": "user",
            "message": {"role": "user", "content":
                "<task-notification>\n<task-id>t-stale</task-id>\n<tool-use-id>toolu_old</tool-use-id>\n<status>completed</status>\n<summary>old finder result</summary>\n</task-notification>"}
        }).to_string();
        match mapper.map_line(&stale) {
            CcMap::Events(events) => {
                let StreamEvent::Chat(chat) = &events[0] else { panic!("expected chat delta") };
                let text = chat.delta_text.as_deref().unwrap_or("");
                assert!(text.contains(STALE_DELIVERY_MARKER), "must carry the marker: {text}");
                assert!(text.contains("t-stale"), "must surface the task id: {text}");
            }
            other => panic!("expected marked event, got {other:?}"),
        }
        assert_eq!(mapper.background_pending(), 0, "stale delivery must not register");
    }

    #[tokio::test]
    async fn turn_waits_for_pending_background_tasks_before_finalizing() {
        // A `result` that lands while a background task is still pending is
        // intermediate: the CLI stays alive, auto-wakes on the
        // task_notification, and ends the turn again with the real final text.
        let script = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/mock_cc_background_continuation.sh"
        );
        let outcome = run_mock_cc(script).await.expect("turn must end on the second result");
        assert_eq!(outcome.final_text.as_deref(), Some("final answer after background agent"));
        assert_eq!(outcome.engine_session_id.as_deref(), Some("cc-sess-bg"));
    }

    #[tokio::test]
    async fn turn_finalizes_when_background_result_is_delivered_inline() {
        // The 2026-10-16 incident, end to end: an auto-backgrounded command
        // whose completion the engine delivers as an ordinary tool_result (not
        // a `<task-notification>` line, not a wake). The inline delivery
        // releases the wait, so the single `result` finalizes the turn instead
        // of being swallowed until the idle watchdog kills the engine.
        let script = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/mock_cc_bg_inline_delivery.sh"
        );
        let outcome = run_mock_cc(script)
            .await
            .expect("inline delivery must release the wait so result finalizes");
        assert_eq!(outcome.final_text.as_deref(), Some("the background command printed DONE"));
        assert_eq!(outcome.engine_session_id.as_deref(), Some("cc-sess-inline"));
    }

    #[tokio::test]
    async fn engine_eof_with_pending_background_tasks_reads_as_explicit_error() {
        // Stdout EOF while background work is pending must not read as a
        // successful turn with the intermediate result as its final text.
        let script = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/mock_cc_bg_pending_eof.sh"
        );
        let error = run_mock_cc(script)
            .await
            .expect_err("EOF with a pending background task must not read as success");
        let text = error.to_string();
        assert!(text.contains("pending background task"), "unexpected error: {text}");
    }

    #[tokio::test]
    async fn denied_background_launch_does_not_wedge_the_turn() {
        // A run_in_background request refused before launch emits no
        // task_started and no receipt; the turn must finalize on its single
        // result instead of swallowing it until the deadline.
        let script = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/mock_cc_bg_denied_launch.sh"
        );
        let outcome = run_mock_cc(script).await.expect("denied launch must not wedge");
        assert_eq!(outcome.final_text.as_deref(), Some("not launched"));
    }

    #[tokio::test]
    async fn notification_without_tool_id_still_releases_the_wait() {
        // Pre-0.3.269 CLI recordings omit tool_use_id from the delivered
        // notification; the task_id correlation from task_started must
        // release the wait.
        let script = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/mock_cc_bg_taskid_notification.sh"
        );
        let outcome = run_mock_cc(script).await.expect("legacy notification must release");
        assert_eq!(outcome.final_text.as_deref(), Some("released via task_id"));
    }

    #[tokio::test]
    async fn result_landing_between_enqueue_and_delivery_stays_intermediate() {
        // Production incident 2026-10-14 replay: two finders completed
        // mid-round (enqueue-time system lines only), the model ended its
        // round with "still waiting for B/C", and the queued deliveries had
        // not landed. That round's result MUST be swallowed — not finalized —
        // and the turn must end on the final round's result after every
        // delivery has landed.
        let script = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/mock_cc_bg_late_delivered_notifications.sh"
        );
        let outcome = run_mock_cc(script)
            .await
            .expect("turn must run until the queued deliveries landed");
        assert_eq!(outcome.final_text.as_deref(), Some("summary table for all three finders"));
    }

    #[tokio::test]
    async fn enqueue_only_protocol_finalizes_through_wake_rounds() {
        // Second production incident 2026-10-14 (session e6f0d46d): every
        // event crossed to BCS — launches, progress, all four results'
        // streamed texts — yet the turn never sent a final: the engine
        // never emits the user <task-notification> lines the release logic
        // was waiting on, so the pending set could never drain and every
        // result stayed swallowed. With wake-driven release the same
        // protocol must run to the real final result.
        let script = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/mock_cc_bg_enqueue_wakes.sh"
        );
        let outcome = tokio::time::timeout(
            std::time::Duration::from_secs(5),
            run_mock_cc(script),
        )
        .await
        .expect("turn must not wedge on an un-drainable pending set")
        .expect("turn must finalize on the last round's result");
        assert_eq!(outcome.final_text.as_deref(), Some("summary table for all three finders"));
    }

    #[tokio::test]
    async fn idle_stream_with_pending_background_fails_explicitly() {
        // The hang shape the same incident left behind: the engine goes
        // permanently silent with a pending background task and neither a
        // result nor a notification will ever come. The sliding idle
        // watchdog must end the turn with a named error that reports the
        // stalled background count — not wedge until the run deadline with
        // BCS waiting on a final that never arrives.
        let script = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/mock_cc_bg_idle_forever.sh"
        );
        let mut req = TurnRequest::new("r-idle", "hi", ".", Arc::new(NullObserver));
        // Include headroom for child startup and fixture scheduling on busy CI;
        // 300ms can expire before task_started reaches the driver.
        req.idle_timeout = Some(std::time::Duration::from_secs(3));
        let engine = ClaudeCode::new(PathBuf::from("bash"), vec![script.to_string()]);
        let (tx, _rx) = tokio::sync::mpsc::channel::<StreamEvent>(64);
        let turn = engine.run_turn(req, tx, tokio_util::sync::CancellationToken::new());
        let error = tokio::time::timeout(std::time::Duration::from_secs(15), turn)
            .await
            .expect("watchdog must fire, not wedge")
            .expect_err("a quiet stream must fail the turn");
        let text = error.to_string();
        assert!(text.contains("idle"), "must name the idle watchdog: {text}");
        assert!(
            text.contains("1 pending background task"),
            "must report the stalled background count: {text}"
        );
    }
