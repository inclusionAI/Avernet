use super::*;

    // ---- B4d: Sent + All directions of list_requests ----

    #[tokio::test]
    async fn list_requests_sent_and_all_with_status_filter() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:sa", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:sa", Some("hi".into()), None)
            .await
            .expect("pending");
        let rid = pending.request_ids[0].clone();

        // Sent: the human caller's outbox has the pending request.
        let sent = svc
            .list_requests("human_1", RequestDirection::Sent, None, 1, 20)
            .await
            .expect("sent ok");
        assert_eq!(sent.total, 1);
        assert_eq!(sent.items[0].request_id, rid);
        assert_eq!(sent.items[0].from_id, "human_1");

        // Sent + Approved filter ⇒ empty (still pending).
        let sent_approved = svc
            .list_requests(
                "human_1",
                RequestDirection::Sent,
                Some(RequestStatus::Approved),
                1,
                20,
            )
            .await
            .expect("sent approved ok");
        assert_eq!(sent_approved.total, 0);

        // All from the human caller's view: inbox (none for human_1) ∪ sent
        // (1) ⇒ total 1.
        let all = svc
            .list_requests("human_1", RequestDirection::All, None, 1, 20)
            .await
            .expect("all ok");
        assert_eq!(all.total, 1);
        assert_eq!(all.items[0].request_id, rid);

        // Approve, then All from the bot's view: inbox (1 approved) ∪ sent
        // (the bot sent nothing) ⇒ total 1, status approved.
        svc.approve(&rid, "85020", None).await.expect("approve");
        let all_bot = svc
            .list_requests("x:sa", RequestDirection::All, None, 1, 20)
            .await
            .expect("all bot ok");
        assert_eq!(all_bot.total, 1);
        assert_eq!(all_bot.items[0].status, RequestStatus::Approved);
    }
    #[tokio::test]
    async fn list_requests_all_filters_reverse_mirror_before_pagination() {
        // All = inbox ∪ sent, deduped by request_id. For a Bot↔Bot connect,
        // the generated reverse row is not a second user-facing request and
        // must be removed before computing the total and slicing pages.
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:btA", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        seed_bot(&db, "x:btB", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("x:btA", "x:btB", None, None)
            .await
            .expect("pending bot↔bot");
        assert_eq!(pending.request_ids.len(), 2);

        // btA's All view contains only the original forward request.
        let all = svc
            .list_requests("x:btA", RequestDirection::All, None, 1, 20)
            .await
            .expect("all ok");
        assert_eq!(all.total, 1);
        assert_eq!(all.items.len(), 1);
        assert_eq!(all.items[0].request_id, pending.request_ids[0]);

        // Filtering happens before pagination: the second page is empty and
        // the total does not include the hidden reverse row.
        let page1 = svc
            .list_requests("x:btA", RequestDirection::All, None, 1, 1)
            .await
            .expect("page1 ok");
        assert_eq!(page1.total, 1);
        assert_eq!(page1.items.len(), 1);
        assert_eq!(page1.items[0].request_id, pending.request_ids[0]);
        let page2 = svc
            .list_requests("x:btA", RequestDirection::All, None, 2, 1)
            .await
            .expect("page2 ok");
        assert_eq!(page2.total, 1);
        assert!(page2.items.is_empty());
    }
