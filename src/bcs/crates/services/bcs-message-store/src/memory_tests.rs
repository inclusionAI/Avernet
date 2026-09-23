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
