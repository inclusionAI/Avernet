
    use super::*;

    fn sample_params(title: Option<&str>) -> NewSessionParams {
        NewSessionParams {
            session_title: title.map(str::to_string),
            ..Default::default()
        }
    }

    fn bot_participant(id: &str, name: &str) -> Participant {
        let mut p = Participant::bot(id, bcs_service_api::ParticipantRole::Consultant);
        p.bot_name = Some(name.to_string());
        p
    }

    #[tokio::test]
    async fn list_by_group_title_filter() {
        let repo = MemorySessionRepo::new();
        repo.create("g1", sample_params(Some("Project Alpha")))
            .await
            .unwrap();
        repo.create("g1", sample_params(Some("Project Beta")))
            .await
            .unwrap();
        repo.create("g1", sample_params(Some("Other")))
            .await
            .unwrap();

        let sessions = repo
            .list_by_group("g1", None, 0, 10, Some("alpha"), None)
            .await;
        assert_eq!(sessions.len(), 1);
        assert_eq!(sessions[0].session_title.as_deref(), Some("Project Alpha"));
    }

    /// `list_by_group` orders by created_at DESC with id DESC tie-break
    /// (mirrors the MySQL `ORDER BY s.gmt_create DESC, s.id DESC`). Two
    /// sessions with explicit ids let us assert the deterministic order:
    /// the later-created session (s2) sorts first whether the timestamps
    /// differ (created_at DESC) or tie (id DESC, larger id first).
    #[tokio::test]
    async fn list_by_group_orders_desc_with_id_tiebreak() {
        let repo = MemorySessionRepo::new();
        let gid = "order-group";
        let s1 = repo
            .create(
                gid,
                NewSessionParams {
                    id: Some(format!("{}:00000001", gid)),
                    session_kind: SessionKind::Chat,
                    ..Default::default()
                },
            )
            .await
            .expect("create s1");
        let s2 = repo
            .create(
                gid,
                NewSessionParams {
                    id: Some(format!("{}:00000002", gid)),
                    session_kind: SessionKind::Chat,
                    ..Default::default()
                },
            )
            .await
            .expect("create s2");

        let listed = repo.list_by_group(gid, None, 0, 10, None, None).await;
        assert_eq!(listed.len(), 2);
        assert_eq!(listed[0].id, s2.id);
        assert_eq!(listed[1].id, s1.id);
    }

    #[tokio::test]
    async fn list_by_group_title_filter_case_insensitive() {
        let repo = MemorySessionRepo::new();
        repo.create("g1", sample_params(Some("PROJECT ALPHA")))
            .await
            .unwrap();
        repo.create("g1", sample_params(Some("beta")))
            .await
            .unwrap();

        let sessions = repo
            .list_by_group("g1", None, 0, 10, Some("alpha"), None)
            .await;
        assert_eq!(sessions.len(), 1);
    }

    #[tokio::test]
    async fn list_by_group_participant_filter() {
        let repo = MemorySessionRepo::new();
        let mut params_a = sample_params(Some("Sess A"));
        params_a.participants = vec![bot_participant("bot_1", "Alice")];
        repo.create("g1", params_a).await.unwrap();

        let mut params_b = sample_params(Some("Sess B"));
        params_b.participants = vec![bot_participant("bot_2", "Bob")];
        repo.create("g1", params_b).await.unwrap();

        let sessions = repo
            .list_by_group("g1", None, 0, 10, None, Some("bot_1"))
            .await;
        assert_eq!(sessions.len(), 1);
        assert_eq!(sessions[0].session_title.as_deref(), Some("Sess A"));
    }

    #[tokio::test]
    async fn list_by_group_title_and_participant_combined() {
        let repo = MemorySessionRepo::new();
        let mut params = sample_params(Some("Task Review"));
        params.participants = vec![bot_participant("human_1", "Human")];
        repo.create("g1", params).await.unwrap();

        // Both filters match
        let sessions = repo
            .list_by_group("g1", None, 0, 10, Some("review"), Some("human_1"))
            .await;
        assert_eq!(sessions.len(), 1);

        // participant matches but title doesn't
        let sessions = repo
            .list_by_group("g1", None, 0, 10, Some("xyz"), Some("human_1"))
            .await;
        assert_eq!(sessions.len(), 0);
    }

    #[tokio::test]
    async fn latest_running_still_works_with_new_params() {
        let repo = MemorySessionRepo::new();
        repo.create("g1", sample_params(None)).await.unwrap();
        // latest_running calls list_by_group internally with None, None
        let latest = repo.latest_running("g1").await;
        assert!(latest.is_some());
    }

    /// Participant filter applies BEFORE pagination. Create 25 sessions
    /// where only the very last one has the target participant, then
    /// query with LIMIT 5 — it should still be found.
    #[tokio::test]
    async fn list_by_group_participant_filter_before_pagination() {
        let repo = MemorySessionRepo::new();
        let target = "human_test_99";
        // Create 24 sessions without the target participant
        for i in 0..24 {
            let mut params = sample_params(Some(&format!("Session {}", i)));
            params.participants = vec![Participant::bot(
                "bot_a",
                bcs_service_api::ParticipantRole::Consultant,
            )];
            repo.create("g1", params).await.unwrap();
        }
        // Create the 25th session WITH the target participant
        let mut params = sample_params(Some("Target Session"));
        params.participants = vec![
            Participant::bot("bot_a", bcs_service_api::ParticipantRole::Consultant),
            {
                let mut p = Participant::human(target, bcs_service_api::ParticipantRole::Observer);
                p.mode = Some(ParticipantMode::Present);
                p
            },
        ];
        repo.create("g1", params).await.unwrap();

        // Query with LIMIT 5 — the target is at position 25 (well past the limit)
        let sessions = repo
            .list_by_group("g1", None, 0, 5, None, Some(target))
            .await;
        assert_eq!(sessions.len(), 1);
        assert_eq!(sessions[0].session_title.as_deref(), Some("Target Session"));
    }

    #[tokio::test]
    async fn collection_collect_then_list_then_uncollect() {
        let repo = MemorySessionRepo::new();
        let gid = "col-group";
        let sess = repo
            .create(
                gid,
                NewSessionParams {
                    session_kind: SessionKind::Chat,
                    participants: vec![Participant::bot(
                        "bot1",
                        bcs_service_api::ParticipantRole::Driver,
                    )],
                    ..Default::default()
                },
            )
            .await
            .expect("create");

        // not collected yet
        let listed = repo
            .list_collected_by_group(gid, "bot1", None, None, 0, 10)
            .await;
        assert!(listed.is_empty());

        repo.collect(&sess.id, "bot1").await.expect("collect");
        let listed = repo
            .list_collected_by_group(gid, "bot1", None, None, 0, 10)
            .await;
        assert_eq!(listed.len(), 1);
        assert_eq!(listed[0].id, sess.id);

        // other bot does not see bot1's collection
        let other = repo
            .list_collected_by_group(gid, "bot2", None, None, 0, 10)
            .await;
        assert!(other.is_empty());

        repo.uncollect(&sess.id, "bot1").await.expect("uncollect");
        let listed = repo
            .list_collected_by_group(gid, "bot1", None, None, 0, 10)
            .await;
        assert!(listed.is_empty());
    }

    /// `list_collected_by_group` orders by collected_at DESC with id DESC
    /// tie-break (mirrors the MySQL `ORDER BY COALESCE(sp.collected_at,
    /// s.gmt_create) DESC, s.id DESC`). The single-session test above never
    /// invokes the comparator (sort_by on <2 elements skips it); this test
    /// collects two sessions so the sort closure runs and the DESC order is
    /// asserted. s2 is collected after s1 and has the larger explicit id, so
    /// it sorts first under both the timestamp and the tie-break.
    #[tokio::test]
    async fn list_collected_by_group_orders_desc_with_id_tiebreak() {
        let repo = MemorySessionRepo::new();
        let gid = "col-order";
        let mk = |id: &str| NewSessionParams {
            id: Some(id.to_string()),
            session_kind: SessionKind::Chat,
            participants: vec![Participant::bot(
                "bot1",
                bcs_service_api::ParticipantRole::Driver,
            )],
            ..Default::default()
        };
        let s1 = repo
            .create(gid, mk(&format!("{}:00000001", gid)))
            .await
            .expect("create s1");
        let s2 = repo
            .create(gid, mk(&format!("{}:00000002", gid)))
            .await
            .expect("create s2");

        repo.collect(&s1.id, "bot1").await.expect("collect s1");
        repo.collect(&s2.id, "bot1").await.expect("collect s2");

        let listed = repo
            .list_collected_by_group(gid, "bot1", None, None, 0, 10)
            .await;
        assert_eq!(listed.len(), 2);
        assert_eq!(listed[0].id, s2.id);
        assert_eq!(listed[1].id, s1.id);
    }

    #[tokio::test]
    async fn collection_non_participant_collect_errors() {
        let repo = MemorySessionRepo::new();
        let gid = "col-group2";
        let sess = repo
            .create(
                gid,
                NewSessionParams {
                    session_kind: SessionKind::Chat,
                    participants: vec![Participant::bot(
                        "bot1",
                        bcs_service_api::ParticipantRole::Driver,
                    )],
                    ..Default::default()
                },
            )
            .await
            .expect("create");
        let err = repo.collect(&sess.id, "not-a-participant").await;
        assert!(err.is_err(), "collect by non-participant must error");
    }

    #[tokio::test]
    async fn collection_uncollect_idempotent_for_non_participant() {
        let repo = MemorySessionRepo::new();
        let gid = "col-group3";
        let sess = repo
            .create(
                gid,
                NewSessionParams {
                    session_kind: SessionKind::Chat,
                    participants: vec![Participant::bot(
                        "bot1",
                        bcs_service_api::ParticipantRole::Driver,
                    )],
                    ..Default::default()
                },
            )
            .await
            .expect("create");
        // uncollect a never-collected, still-participant session -> Ok
        repo.uncollect(&sess.id, "bot1")
            .await
            .expect("uncollect not collected ok");
        // uncollect a non-participant -> Ok (idempotent)
        repo.uncollect(&sess.id, "nobody")
            .await
            .expect("uncollect non-participant ok");
    }

    #[tokio::test]
    async fn collection_respects_status_and_title_filter() {
        let repo = MemorySessionRepo::new();
        let gid = "col-group4";
        let s_running = repo
            .create(
                gid,
                NewSessionParams {
                    session_kind: SessionKind::Chat,
                    session_title: Some("Alpha Report".into()),
                    participants: vec![Participant::bot(
                        "bot1",
                        bcs_service_api::ParticipantRole::Driver,
                    )],
                    ..Default::default()
                },
            )
            .await
            .expect("create");
        let s_to_complete = repo
            .create(
                gid,
                NewSessionParams {
                    session_kind: SessionKind::Chat,
                    session_title: Some("Beta Note".into()),
                    participants: vec![Participant::bot(
                        "bot1",
                        bcs_service_api::ParticipantRole::Driver,
                    )],
                    ..Default::default()
                },
            )
            .await
            .expect("create");
        repo.complete_if_running(&s_to_complete.id, None, None)
            .await
            .expect("complete");
        repo.collect(&s_running.id, "bot1")
            .await
            .expect("collect running");
        repo.collect(&s_to_complete.id, "bot1")
            .await
            .expect("collect completed");

        let only_running = repo
            .list_collected_by_group(gid, "bot1", Some(SessionStatus::Running), None, 0, 10)
            .await;
        assert_eq!(only_running.len(), 1);
        assert_eq!(only_running[0].id, s_running.id);

        let only_alpha = repo
            .list_collected_by_group(gid, "bot1", None, Some("alpha"), 0, 10)
            .await;
        assert_eq!(only_alpha.len(), 1);
        assert_eq!(only_alpha[0].id, s_running.id);
    }

    #[tokio::test]
    async fn collection_lost_when_participant_removed() {
        let repo = MemorySessionRepo::new();
        let gid = "col-group5";
        let sess = repo
            .create(
                gid,
                NewSessionParams {
                    session_kind: SessionKind::Chat,
                    participants: vec![Participant::bot(
                        "bot1",
                        bcs_service_api::ParticipantRole::Driver,
                    )],
                    ..Default::default()
                },
            )
            .await
            .expect("create");
        repo.collect(&sess.id, "bot1").await.expect("collect");
        assert_eq!(
            repo.list_collected_by_group(gid, "bot1", None, None, 0, 10)
                .await
                .len(),
            1
        );
        repo.remove_participant(&sess.id, "bot1")
            .await
            .expect("remove");
        // after leaving, collection mark is gone (memory set must be pruned)
        assert!(
            repo.list_collected_by_group(gid, "bot1", None, None, 0, 10)
                .await
                .is_empty()
        );
    }

    /// `count_by_group` MUST mirror `list_by_group`'s filters and return the
    /// total (pre-pagination) count, not the paginated subset length.
    #[tokio::test]
    async fn count_by_group_matches_list_filters_without_pagination() {
        let repo = MemorySessionRepo::new();
        // session in another group must be excluded from g1 counts
        repo.create("other", sample_params(Some("Other")))
            .await
            .unwrap();

        // 5 sessions in "g1" with mixed status / title / participant
        let mut p1 = sample_params(Some("Alpha"));
        p1.participants = vec![bot_participant("bot_1", "Alice")];
        repo.create("g1", p1).await.unwrap(); // Alpha, Running, bot_1

        let mut p2 = sample_params(Some("Alpha Beta"));
        p2.participants = vec![bot_participant("bot_2", "Bob")];
        repo.create("g1", p2).await.unwrap(); // Alpha Beta, Running, bot_2

        let mut p3 = sample_params(Some("Beta"));
        p3.participants = vec![bot_participant("bot_1", "Alice")];
        let s3 = repo.create("g1", p3).await.unwrap();
        repo.complete_if_running(&s3.id, None, None).await.unwrap(); // Beta, Completed, bot_1

        let mut p4 = sample_params(Some("Gamma"));
        p4.participants = vec![bot_participant("bot_3", "Carol")];
        repo.create("g1", p4).await.unwrap(); // Gamma, Running, bot_3

        let mut p5 = sample_params(Some("Alpha Gamma"));
        p5.participants = vec![bot_participant("bot_1", "Alice")];
        let s5 = repo.create("g1", p5).await.unwrap();
        repo.complete_if_running(&s5.id, None, None).await.unwrap(); // Alpha Gamma, Completed, bot_1

        // No filters → all 5 in g1 (other-group session excluded)
        assert_eq!(
            repo.count_by_group("g1", None, None, None).await.unwrap(),
            5
        );

        // Count is NOT the paginated subset
        let page = repo.list_by_group("g1", None, 0, 2, None, None).await;
        assert_eq!(page.len(), 2);
        assert_eq!(
            repo.count_by_group("g1", None, None, None).await.unwrap(),
            5
        );

        // Status filter: Running only → s1, s2, s4 = 3
        assert_eq!(
            repo.count_by_group("g1", Some(SessionStatus::Running), None, None)
                .await
                .unwrap(),
            3
        );

        // Title filter: "alpha" (case-insensitive) → s1, s2, s5 = 3
        assert_eq!(
            repo.count_by_group("g1", None, Some("alpha"), None)
                .await
                .unwrap(),
            3
        );

        // Participant filter: bot_1 → s1, s3, s5 = 3
        assert_eq!(
            repo.count_by_group("g1", None, None, Some("bot_1"))
                .await
                .unwrap(),
            3
        );

        // Combined: Running + "alpha" + bot_1 → only s1 = 1
        assert_eq!(
            repo.count_by_group(
                "g1",
                Some(SessionStatus::Running),
                Some("alpha"),
                Some("bot_1")
            )
            .await
            .unwrap(),
            1
        );

        // count_by_group must equal list_by_group total (large limit) for each combo
        let combos: [(Option<SessionStatus>, Option<&str>, Option<&str>); 5] = [
            (None, None, None),
            (Some(SessionStatus::Running), None, None),
            (None, Some("alpha"), None),
            (None, None, Some("bot_1")),
            (Some(SessionStatus::Running), Some("alpha"), Some("bot_1")),
        ];
        for (status, title, pid) in combos {
            let listed = repo.list_by_group("g1", status, 0, 1000, title, pid).await;
            let counted = repo.count_by_group("g1", status, title, pid).await.unwrap();
            assert_eq!(
                listed.len() as u64,
                counted,
                "list vs count mismatch for status={status:?} title={title:?} pid={pid:?}"
            );
        }
    }
