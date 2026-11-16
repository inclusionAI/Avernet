    use super::*;
    use bcs_domain::SenderType;

    fn make_msg(session_id: &str, message_id: &str, session_seq: i64) -> PersistedMessage {
        PersistedMessage {
            message_id: message_id.to_string(),
            group_id: "g1".to_string(),
            session_id: session_id.to_string(),
            session_seq,
            sender_id: "bot1".to_string(),
            sender_type: SenderType::Bot,
            message_type: "chat".to_string(),
            content: serde_json::json!(format!("msg-{message_id}")),
            client_msg_id: None,
            owner_bot_id: None,
            visibility_domain: Some(MessageVisibilityDomain::Chat),
            audience: None,
            status: PersistedMessageStatus::Normal,
            created_at: session_seq as u64 * 1000,
            run_id: String::new(),
        }
    }

    /// `query_messages` (old compat API) must remain DESC-by-created_at and
    /// unaffected by the new ASC method.
    #[tokio::test]
    async fn query_messages_still_desc_after_new_method() {
        let repo = MemoryMessageRepo::new();
        {
            let mut sessions = repo.sessions.write().await;
            let entry = sessions.entry("s2".to_string()).or_default();
            entry.messages.push(make_msg("s2", "a", 1));
            entry.messages.push(make_msg("s2", "b", 2));
            entry.messages.push(make_msg("s2", "c", 3));
            entry.seq = 3;
        }
        let page = repo
            .query_messages(MessageQuery {
                group_id: "g1".to_string(),
                session_id: "s2".to_string(),
                cursor: None,
                limit: 10,
                keyword: None,
                sender_id: None,
                message_type: None,
                owner_filter: MessageOwnerFilter::Any,
                time_range: None,
                visible_from_seq: None,
                human_view: None,
            })
            .await
            .unwrap();
        // created_at DESC, session_seq DESC → [3, 2, 1]
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![3, 2, 1]
        );
    }

    /// `list_session_history` must mirror the legacy direct-read contract:
    /// `created_at DESC, session_seq DESC` order, the full 3-state
    /// `MessageOwnerFilter` (incl. `IsNull`), `visible_from_seq` cutoff, an
    /// exclusive composite `(created_at, session_seq)` `before` cursor
    /// (VYQHI), and `has_more` + `next_cursor` instead of a count estimate.
    #[tokio::test]
    async fn list_session_history_desc_cutoff_and_cursor() {
        let repo = MemoryMessageRepo::new();
        // Seed: seq 1..5, created_at = seq * 1000 so order is unambiguous.
        // Mix owner_bot_id: odd seqs are NULL-owned, even seqs owned by "bot-w".
        {
            let mut sessions = repo.sessions.write().await;
            let entry = sessions.entry("s3".to_string()).or_default();
            for seq in 1..=5i64 {
                let mut m = make_msg("s3", &format!("h{seq}"), seq);
                m.created_at = seq as u64 * 1000;
                m.owner_bot_id = if seq % 2 == 0 {
                    Some("bot-w".to_string())
                } else {
                    None
                };
                entry.messages.push(m);
            }
            entry.seq = 5;
        }

        // Plain list: DESC by created_at (= seq DESC), all 5, no more.
        let page = repo
            .list_session_history("s3", MessageOwnerFilter::Any, None, None, None, 50)
            .await
            .unwrap();
        assert!(!page.has_more);
        assert!(page.next_cursor.is_none());
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![5, 4, 3, 2, 1]
        );

        // IsNull filter: only NULL-owned (odd seqs) survive, still DESC.
        let page = repo
            .list_session_history("s3", MessageOwnerFilter::IsNull, None, None, None, 50)
            .await
            .unwrap();
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![5, 3, 1]
        );

        // Eq filter: only bot-w-owned (even seqs) survive.
        let page = repo
            .list_session_history(
                "s3",
                MessageOwnerFilter::Eq("bot-w".to_string()),
                None,
                None,
                None,
                50,
            )
            .await
            .unwrap();
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![4, 2]
        );

        // visible_from_seq=3: drop seqs 1,2; DESC → [5,4,3].
        let page = repo
            .list_session_history("s3", MessageOwnerFilter::Any, Some(3), None, None, 50)
            .await
            .unwrap();
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![5, 4, 3]
        );

        // before=(3000, i64::MIN) (exclusive created_at == 3000): only
        // created_at < 3000 → [2,1]. The MIN session_seq sentinel makes the
        // composite bound behave like the legacy created_at-only strict-less.
        let page = repo
            .list_session_history(
                "s3",
                MessageOwnerFilter::Any,
                None,
                None,
                Some((3000, i64::MIN)),
                50,
            )
            .await
            .unwrap();
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![2, 1]
        );

        // limit=2 with has_more + next_cursor = (4000, 4).
        let page = repo
            .list_session_history("s3", MessageOwnerFilter::Any, None, None, None, 2)
            .await
            .unwrap();
        assert!(page.has_more);
        assert_eq!(page.next_cursor, Some((4000, 4)));
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![5, 4]
        );

        // Follow the cursor: before=(4000,4) → [3,2], still has_more (1 left).
        let page = repo
            .list_session_history(
                "s3",
                MessageOwnerFilter::Any,
                None,
                None,
                Some((4000, 4)),
                2,
            )
            .await
            .unwrap();
        assert!(page.has_more);
        assert_eq!(page.next_cursor, Some((2000, 2)));
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![3, 2]
        );

        // Final page: before=(2000,2) → [1], no more.
        let page = repo
            .list_session_history(
                "s3",
                MessageOwnerFilter::Any,
                None,
                None,
                Some((2000, 2)),
                2,
            )
            .await
            .unwrap();
        assert!(!page.has_more);
        assert!(page.next_cursor.is_none());
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![1]
        );

        // Unknown session → empty page.
        let page = repo
            .list_session_history("nope", MessageOwnerFilter::Any, None, None, None, 10)
            .await
            .unwrap();
        assert!(page.messages.is_empty());
        assert!(!page.has_more);
    }

    /// VYQHI regression: messages sharing the same `created_at` at a page
    /// boundary must not be skipped when following the composite cursor.
    #[tokio::test]
    async fn list_session_history_tied_created_at_no_skip() {
        let repo = MemoryMessageRepo::new();
        // Seed 5 messages ALL with the same created_at; session_seq breaks ties.
        {
            let mut sessions = repo.sessions.write().await;
            let entry = sessions.entry("stie".to_string()).or_default();
            for seq in 1..=5i64 {
                let mut m = make_msg("stie", &format!("t{seq}"), seq);
                m.created_at = 9_000; // identical for every message
                entry.messages.push(m);
            }
            entry.seq = 5;
        }

        // Page 1 (limit 2): [5, 4], next_cursor = (9000, 4).
        let page = repo
            .list_session_history("stie", MessageOwnerFilter::Any, None, None, None, 2)
            .await
            .unwrap();
        assert!(page.has_more);
        assert_eq!(page.next_cursor, Some((9_000, 4)));
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![5, 4]
        );

        // Page 2: before=(9000,4) → [3, 2], next_cursor = (9000, 2).
        let page = repo
            .list_session_history(
                "stie",
                MessageOwnerFilter::Any,
                None,
                None,
                Some((9_000, 4)),
                2,
            )
            .await
            .unwrap();
        assert!(page.has_more);
        assert_eq!(page.next_cursor, Some((9_000, 2)));
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![3, 2]
        );

        // Page 3: before=(9000,2) → [1], no more.
        let page = repo
            .list_session_history(
                "stie",
                MessageOwnerFilter::Any,
                None,
                None,
                Some((9_000, 2)),
                2,
            )
            .await
            .unwrap();
        assert!(!page.has_more);
        assert!(page.next_cursor.is_none());
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![1]
        );
    }

    #[tokio::test]
    async fn participant_audience_filter_runs_before_pagination() {
        let repo = MemoryMessageRepo::new();
        {
            let mut sessions = repo.sessions.write().await;
            let entry = sessions.entry("scoped".to_string()).or_default();
            let audiences = [
                MessageAudience::Public,
                MessageAudience::directed(["human_a"]).unwrap(),
                MessageAudience::FullOnly,
                MessageAudience::Public,
                MessageAudience::FullOnly,
            ];
            for (index, audience) in audiences.into_iter().enumerate() {
                let seq = index as i64 + 1;
                let mut message = make_msg("scoped", &format!("m{seq}"), seq);
                message.visibility_domain = Some(MessageVisibilityDomain::StateMachine);
                message.audience = Some(audience);
                entry.messages.push(message);
            }
            entry.seq = 5;
        }

        let view = HumanMessageView {
            actor_id: "human_a".to_string(),
            scope: bcs_domain::MessageViewScope::Participant,
            allow_legacy_unclassified_chat: false,
        };
        let first = repo
            .list_session_history(
                "scoped",
                MessageOwnerFilter::Any,
                None,
                Some(view.clone()),
                None,
                2,
            )
            .await
            .unwrap();

        assert_eq!(
            first
                .messages
                .iter()
                .map(|message| message.session_seq)
                .collect::<Vec<_>>(),
            vec![4, 2],
        );
        assert!(first.has_more);
        assert_eq!(first.next_cursor, Some((2_000, 2)));

        let second = repo
            .list_session_history(
                "scoped",
                MessageOwnerFilter::Any,
                None,
                Some(view),
                first.next_cursor,
                2,
            )
            .await
            .unwrap();
        assert_eq!(
            second
                .messages
                .iter()
                .map(|message| message.session_seq)
                .collect::<Vec<_>>(),
            vec![1],
        );
        assert!(!second.has_more);
    }

    /// Seed one session's persisted log for the activity-key tests. The
    /// messages are appended directly (like the fixtures above) so each row
    /// controls its own `created_at` and group:
    async fn seed_activity(
        repo: &MemoryMessageRepo,
        group_id: &str,
        session_id: &str,
        created_at: &[u64],
    ) {
        let mut sessions = repo.sessions.write().await;
        let entry = sessions.entry(session_id.to_string()).or_default();
        for (index, at) in created_at.iter().enumerate() {
            let seq = entry.seq + 1;
            entry.seq = seq;
            let mut message = make_msg(session_id, &format!("{session_id}-{index}"), seq);
            message.group_id = group_id.to_string();
            message.created_at = *at;
            entry.messages.push(message);
        }
    }

    /// Empty id lists short-circuit to an empty map instead of scanning or
    /// querying for nothing.
    #[tokio::test]
    async fn last_activity_empty_id_list_is_an_empty_map() {
        let repo = MemoryMessageRepo::new();
        seed_activity(&repo, "g-a", "s-a", &[1_000]).await;

        assert!(repo.last_activity_by_group_ids(&[]).await.unwrap().is_empty());
        assert!(repo.last_activity_by_session_ids(&[]).await.unwrap().is_empty());
    }

    /// Per-id activity is `MAX(created_at)` in epoch ms — not the first, the
    /// last appended, nor the session sequence.
    #[tokio::test]
    async fn last_activity_returns_max_created_at_per_group_and_session() {
        let repo = MemoryMessageRepo::new();
        seed_activity(&repo, "g-a", "s-a", &[1_000, 5_000, 3_000]).await;
        seed_activity(&repo, "g-b", "s-b", &[2_000, 7_000, 4_000]).await;

        let groups = repo
            .last_activity_by_group_ids(&["g-a".to_string(), "g-b".to_string()])
            .await
            .unwrap();
        assert_eq!(groups.len(), 2);
        assert_eq!(groups.get("g-a"), Some(&5_000));
        assert_eq!(groups.get("g-b"), Some(&7_000));

        let sessions = repo
            .last_activity_by_session_ids(&["s-a".to_string(), "s-b".to_string()])
            .await
            .unwrap();
        assert_eq!(sessions.len(), 2);
        assert_eq!(sessions.get("s-a"), Some(&5_000));
        assert_eq!(sessions.get("s-b"), Some(&7_000));
    }

    /// Ids without any persisted message are absent from the map so callers
    /// keep their own `created_at` fallback (design D1).
    #[tokio::test]
    async fn last_activity_omits_ids_without_messages() {
        let repo = MemoryMessageRepo::new();
        seed_activity(&repo, "g-known", "s-known", &[4_200]).await;

        let groups = repo
            .last_activity_by_group_ids(&["g-known".to_string(), "g-missing".to_string()])
            .await
            .unwrap();
        assert_eq!(groups.len(), 1);
        assert_eq!(groups.get("g-known"), Some(&4_200));
        assert!(!groups.contains_key("g-missing"));

        let sessions = repo
            .last_activity_by_session_ids(&["s-known".to_string(), "s-missing".to_string()])
            .await
            .unwrap();
        assert_eq!(sessions.len(), 1);
        assert_eq!(sessions.get("s-known"), Some(&4_200));
        assert!(!sessions.contains_key("s-missing"));
    }

    /// More distinct ids than one SQL chunk (200) must all be answered; the
    /// memory store has no chunking, this guards the shared contract.
    #[tokio::test]
    async fn last_activity_covers_ids_beyond_one_chunk() {
        let repo = MemoryMessageRepo::new();
        for index in 0..250_u64 {
            seed_activity(
                &repo,
                &format!("g-{index:03}"),
                &format!("s-{index:03}"),
                &[1_000 + index],
            )
            .await;
        }

        let group_ids: Vec<String> = (0..250).map(|index| format!("g-{index:03}")).collect();
        let groups = repo.last_activity_by_group_ids(&group_ids).await.unwrap();
        assert_eq!(groups.len(), 250);
        assert_eq!(groups.get("g-000"), Some(&1_000));
        assert_eq!(groups.get("g-249"), Some(&1_249));

        let session_ids: Vec<String> = (0..250).map(|index| format!("s-{index:03}")).collect();
        let sessions = repo.last_activity_by_session_ids(&session_ids).await.unwrap();
        assert_eq!(sessions.len(), 250);
        assert_eq!(sessions.get("s-000"), Some(&1_000));
        assert_eq!(sessions.get("s-249"), Some(&1_249));
    }

    /// A candidate with thousands of messages next to shallow ones must still
    /// report its own maximum.
    #[tokio::test]
    async fn last_activity_deep_history_candidate_uses_its_max() {
        let repo = MemoryMessageRepo::new();
        let deep: Vec<u64> = (1..=3_000).map(|value| 1_000 + value).collect();
        seed_activity(&repo, "g-deep", "s-deep", &deep).await;
        seed_activity(&repo, "g-shallow", "s-shallow", &[9_000_000]).await;

        let groups = repo
            .last_activity_by_group_ids(&["g-deep".to_string(), "g-shallow".to_string()])
            .await
            .unwrap();
        assert_eq!(groups.get("g-deep"), Some(&4_000));
        assert_eq!(groups.get("g-shallow"), Some(&9_000_000));

        let sessions = repo
            .last_activity_by_session_ids(&["s-deep".to_string(), "s-shallow".to_string()])
            .await
            .unwrap();
        assert_eq!(sessions.get("s-deep"), Some(&4_000));
        assert_eq!(sessions.get("s-shallow"), Some(&9_000_000));
    }

    /// Activity is store-owned per env (the same isolation the history reads
    /// rely on): another env's messages for the same ids never leak in.
    #[tokio::test]
    async fn last_activity_never_counts_another_environment() {
        let dev = MemoryMessageRepo::new().with_environment("dev".to_string());
        let prod = MemoryMessageRepo::new().with_environment("prod".to_string());
        seed_activity(&dev, "g-shared", "s-shared", &[1_000]).await;
        seed_activity(&prod, "g-shared", "s-shared", &[9_000]).await;
        seed_activity(&prod, "g-prod-only", "s-prod-only", &[9_000]).await;

        let groups = dev
            .last_activity_by_group_ids(&["g-shared".to_string(), "g-prod-only".to_string()])
            .await
            .unwrap();
        assert_eq!(groups.len(), 1);
        assert_eq!(groups.get("g-shared"), Some(&1_000));

        let sessions = dev
            .last_activity_by_session_ids(&["s-shared".to_string(), "s-prod-only".to_string()])
            .await
            .unwrap();
        assert_eq!(sessions.len(), 1);
        assert_eq!(sessions.get("s-shared"), Some(&1_000));
    }

    /// In-group per-session activity (D5 revised): `MAX(created_at)` per
    /// session within exactly one group — a session in another group never
    /// leaks into this group's map.
    #[tokio::test]
    async fn last_activity_in_group_returns_max_created_at_per_session() {
        let repo = MemoryMessageRepo::new();
        seed_activity(&repo, "g-a", "s-a1", &[1_000, 5_000, 3_000]).await;
        seed_activity(&repo, "g-a", "s-a2", &[2_000]).await;
        seed_activity(&repo, "g-b", "s-b1", &[9_000]).await;

        let activity = repo.last_activity_in_group("g-a").await.unwrap();
        assert_eq!(activity.len(), 2);
        assert_eq!(activity.get("s-a1"), Some(&5_000));
        assert_eq!(activity.get("s-a2"), Some(&2_000));
        assert!(!activity.contains_key("s-b1"));
        assert!(repo.last_activity_in_group("g-missing").await.unwrap().is_empty());
    }

    /// Sessions with no persisted message in the group are absent from the
    /// map so callers keep the session's own `created_at` fallback (D1).
    #[tokio::test]
    async fn last_activity_in_group_omits_sessions_without_messages() {
        let repo = MemoryMessageRepo::new();
        seed_activity(&repo, "g-a", "s-with-messages", &[4_200]).await;

        let activity = repo.last_activity_in_group("g-a").await.unwrap();
        assert_eq!(activity.len(), 1);
        assert!(!activity.contains_key("s-without-messages"));
    }

    /// The same env isolation as the id-batched reads: another env's
    /// messages for an identical group/session id never leak in.
    #[tokio::test]
    async fn last_activity_in_group_never_counts_another_environment() {
        let dev = MemoryMessageRepo::new().with_environment("dev".to_string());
        let prod = MemoryMessageRepo::new().with_environment("prod".to_string());
        seed_activity(&dev, "g-shared", "s-shared", &[1_000]).await;
        seed_activity(&prod, "g-shared", "s-shared", &[9_000]).await;

        let activity = dev.last_activity_in_group("g-shared").await.unwrap();
        assert_eq!(activity.len(), 1);
        assert_eq!(activity.get("s-shared"), Some(&1_000));
        assert_eq!(
            prod.last_activity_in_group("g-shared").await.unwrap().get("s-shared"),
            Some(&9_000)
        );
    }
