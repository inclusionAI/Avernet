use super::*;

    #[tokio::test]
    async fn admission_bot_not_found() {
        let (eg, pp, _rq, bc, _db) = assemble().await;
        let svc = admission_service(&eg, &bc, &pp);
        let r = svc
            .check_admission("human_1", "x:missing", "originator", "dev")
            .await
            .expect("bot-not-found deny result");
        assert!(!r.allowed);
        assert!(r.grants.is_empty());
        assert_eq!(r.reason_code, AdmissionReason::BotNotFound);
        assert!(!r.public_default);
    }
    #[tokio::test]
    async fn admission_bot_hidden() {
        let (eg, pp, _rq, bc, db) = assemble().await;
        seed_bot(&db, "x:hid", "public", "protected", "OPEN", "hidden", Some("85020")).await;
        let svc = admission_service(&eg, &bc, &pp);
        let r = svc
            .check_admission("human_1", "x:hid", "originator", "dev")
            .await
            .expect("hidden deny result");
        assert!(!r.allowed);
        assert!(r.grants.is_empty());
        assert_eq!(r.reason_code, AdmissionReason::BotHidden);
        assert!(!r.public_default);
    }
    #[tokio::test]
    async fn admission_friend_edge_allowed() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:fr", "protected", "protected", "OPEN", "online", Some("85020")).await;
        // Seed a friend edge by going through ConnectService (auto path),
        // which also ensures the target default profile and builds the edge.
        let conn = service(&eg, &pp, &rq, &bc);
        conn.create_connect("human_1", "x:fr", None, None)
            .await
            .expect("connect");
        assert!(eg.has_friend_edge("human_1", "x:fr", "dev").await);

        let svc = admission_service(&eg, &bc, &pp);
        let r = svc
            .check_admission("human_1", "x:fr", "originator", "dev")
            .await
            .expect("friend-edge allow");
        assert!(r.allowed);
        assert_eq!(r.reason_code, AdmissionReason::Ok);
        assert!(!r.public_default, "friend-edge path is not public_default");
        assert_eq!(r.grants.len(), 1);
        assert_eq!(r.grants[0].source, GrantSource::EdgeGrant);
        assert_eq!(r.grants[0].kind, GrantKind::PermissionProfile);
        // revision/digest enriched from the profile store.
        assert!(r.grants[0].revision.is_some(), "revision enriched");
        assert!(r.grants[0].digest.is_some(), "digest enriched");
        // ref_id matches the cached default profile id.
        let default_id = eg.get_default_profile_id("x:fr", "dev").await.unwrap();
        assert_eq!(r.grants[0].ref_id, default_id);
    }
    #[tokio::test]
    async fn admission_public_default() {
        let (eg, pp, _rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pub", "public", "protected", "OPEN", "online", Some("85020")).await;
        let svc = admission_service(&eg, &bc, &pp);
        let r = svc
            .check_admission("human_1", "x:pub", "originator", "dev")
            .await
            .expect("public_default allow");
        assert!(r.allowed);
        assert_eq!(r.reason_code, AdmissionReason::PublicDefault);
        assert!(r.public_default);
        assert_eq!(r.grants.len(), 1);
        assert_eq!(r.grants[0].source, GrantSource::PublicDefault);
        assert!(r.grants[0].revision.is_some());
        assert!(r.grants[0].digest.is_some());
    }
    #[tokio::test]
    async fn admission_no_edge_protected_bot() {
        let (eg, pp, _rq, bc, db) = assemble().await;
        seed_bot(&db, "x:prot", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = admission_service(&eg, &bc, &pp);
        let r = svc
            .check_admission("human_1", "x:prot", "originator", "dev")
            .await
            .expect("no-edge deny");
        assert!(!r.allowed);
        assert!(r.grants.is_empty());
        assert_eq!(r.reason_code, AdmissionReason::NoEdge);
        assert!(!r.public_default);
    }
    #[tokio::test]
    async fn build_authz_context_with_active_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:az", "protected", "protected", "OPEN", "online", Some("85020")).await;
        // Seed an approved friend edge.
        let conn = service(&eg, &pp, &rq, &bc);
        conn.create_connect("human_1", "x:az", None, None)
            .await
            .expect("connect");

        let svc = admission_service(&eg, &bc, &pp);
        let ctx = svc
            .build_authz_context("human_1", "x:az", "o", "task_1", "run_1", "dev")
            .await
            .expect("authz ctx");
        assert_eq!(ctx.from_id, "human_1");
        assert_eq!(ctx.to_id, "x:az");
        assert_eq!(ctx.task_id, "task_1");
        assert_eq!(ctx.run_id, "run_1");
        assert_eq!(ctx.env, "dev");
        assert_eq!(ctx.originator, "o");
        assert!(ctx.signature.is_none());
        assert!(ctx.grants.len() >= 1, "active edge present in grants");
        assert_eq!(ctx.grants[0].source, GrantSource::EdgeGrant);
        assert_eq!(ctx.grants[0].kind, GrantKind::PermissionProfile);
    }
    #[tokio::test]
    async fn build_authz_context_empty_for_protected_no_edge() {
        let (eg, pp, _rq, bc, db) = assemble().await;
        seed_bot(&db, "x:prot2", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = admission_service(&eg, &bc, &pp);
        let ctx = svc
            .build_authz_context("human_1", "x:prot2", "o", "t", "r", "dev")
            .await
            .expect("ctx");
        assert!(
            ctx.grants.is_empty(),
            "protected bot with no edge: no grants, no public_default fallback"
        );
    }
    #[tokio::test]
    async fn build_authz_context_public_default_fallback() {
        let (eg, pp, _rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pub2", "public", "protected", "OPEN", "online", Some("85020")).await;
        let svc = admission_service(&eg, &bc, &pp);
        let ctx = svc
            .build_authz_context("human_1", "x:pub2", "o", "t", "r", "dev")
            .await
            .expect("ctx");
        assert_eq!(ctx.grants.len(), 1, "public bot: public_default grant injected");
        assert_eq!(ctx.grants[0].source, GrantSource::PublicDefault);
    }
    // ---- B4b: is_authorized distinguishes Rules-edge admission from friend ----

    #[tokio::test]
    async fn is_authorized_true_for_rules_edge_while_has_friend_edge_false() {
        // A GrantKind::Rules edge (NOT a default-profile edge) must admit via
        // `is_authorized` but NOT via `has_friend_edge`. This is the B4b
        // conformance check: admission uses is_authorized (edge superset),
        // friendship uses has_friend_edge (default-profile only).
        let (eg, pp, _rq, _bc, db) = assemble().await;
        seed_bot(&db, "x:rules", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        // Ensure the target has a default profile so has_friend_edge can resolve,
        // then insert a Rules edge (grant_kind=Rules, arbitrary ref) from→to.
        pp.ensure_default_profile("x:rules", "dev").await.expect("ensure default");
        eg.insert_grant(EdgeGrant {
            edge_id: 5001,
            env: "dev".to_string(),
            from_id: "human_1".to_string(),
            to_id: "x:rules".to_string(),
            grant_kind: GrantKind::Rules,
            grant_ref_id: 5003,
            rules: None,
            status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::Any,
            originator_policy_data: None,
        })
        .await
        .expect("insert rules edge");

        // is_authorized ⇒ true (any active edge from→to).
        assert!(
            eg.is_authorized("human_1", "x:rules", "dev").await,
            "Rules edge must authorize via is_authorized"
        );
        // has_friend_edge ⇒ false (Rules edge is not a default-profile friend edge).
        assert!(
            !eg.has_friend_edge("human_1", "x:rules", "dev").await,
            "Rules edge is not a friend edge (D12 default-profile only)"
        );
        // list_active_grants surfaces the Rules edge.
        let active = eg.list_active_grants("human_1", "x:rules", "dev").await;
        assert_eq!(active.len(), 1);
        assert_eq!(active[0].grant_kind, GrantKind::Rules);
    }
    #[tokio::test]
    async fn admission_admits_via_rules_edge_without_friend_edge() {
        // Admission must admit via a Rules edge (is_authorized superset), even
        // when no friend (default-profile) edge exists. This is the behavioral
        // proof that check_admission uses is_authorized, not has_friend_edge.
        let (eg, pp, _rq, bc, db) = assemble().await;
        seed_bot(&db, "x:radm", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        pp.ensure_default_profile("x:radm", "dev").await.expect("ensure default");
        eg.insert_grant(EdgeGrant {
            edge_id: 5002,
            env: "dev".to_string(),
            from_id: "human_1".to_string(),
            to_id: "x:radm".to_string(),
            grant_kind: GrantKind::Rules,
            grant_ref_id: 5004,
            rules: None,
            status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::Any,
            originator_policy_data: None,
        })
        .await
        .expect("insert rules edge");

        let svc = admission_service(&eg, &bc, &pp);
        let r = svc
            .check_admission("human_1", "x:radm", "originator", "dev")
            .await
            .expect("rules-edge admission");
        assert!(r.allowed, "Rules edge must admit via is_authorized");
        assert_eq!(r.reason_code, AdmissionReason::Ok);
        assert!(
            !r.public_default,
            "Rules-edge admission is not the public_default path"
        );
    }
