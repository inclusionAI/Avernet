use super::*;

fn context(session: Option<&str>) -> BotRunContext {
    BotRunContext {
        run_id: "accepted-run".into(), bot_id: "worker".into(), group_id: "group".into(),
        bcs_session_id: session.map(str::to_string), deadline_ms: u64::MAX, terminal: false,
    }
}

#[test]
fn missing_legacy_session_preserves_the_accepted_group_or_session_scope() {
    for session in [None, Some("group"), Some("group:00000000"), Some("group:session"), Some("opaque-session")] {
        let scope = registered_event_scope(context(session), "worker", "group", None).unwrap();
        assert_eq!(scope, ("group".into(), Some(session.unwrap_or("group").into())));
        // A returned scope must suppress the synthetic default fallback.
        assert!(scope.1.is_some());
    }
}

#[test]
fn explicit_and_v2_embedded_sessions_must_match_the_accepted_scope() {
    for (wire_group, explicit) in [
        ("group", Some("group:session")), ("group:session", None),
        ("group:session", Some("group")), ("group:session", Some("group:session")),
    ] {
        assert_eq!(registered_event_scope(context(Some("group:session")), "worker", wire_group, explicit).unwrap(),
            ("group".into(), Some("group:session".into())));
    }
    assert!(registered_event_scope(context(Some("group")), "worker", "group", Some("group")).is_ok());
}

#[test]
fn trusted_run_scope_cannot_repair_wrong_bot_group_or_explicit_session() {
    for session in ["group", "group:session", "opaque-session"] {
        for (bot, group, explicit) in [
            ("other-worker", "group", None),
            ("worker", "other-group", None),
            ("worker", "other-group", Some(session)),
            ("worker", "group", Some("other-group:session")),
            ("worker", "group", Some("group:other")),
            ("worker", "group:other", None),
            ("worker", "group", Some("")),
        ] {
            assert!(registered_event_scope(context(Some(session)), bot, group, explicit).is_err(),
                "must reject {bot}/{group}/{explicit:?} for {session}");
        }
    }
    // A true group-only operation cannot borrow an explicit default Session.
    assert!(registered_event_scope(context(Some("group")), "worker", "group", Some("group:00000000")).is_err());
}

#[test]
fn direct_a2a_keeps_its_existing_opaque_session_contract() {
    for session in ["bcs-cli:caller:abcdef", "chat:abcdef", "opaque"] {
        let mut run = context(Some(session));
        run.group_id.clear();
        assert_eq!(registered_event_scope(run, "worker", session, None).unwrap(),
            (String::new(), Some(session.into())));
    }
}
