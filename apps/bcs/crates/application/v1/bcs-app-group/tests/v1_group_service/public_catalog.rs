use super::*;

#[tokio::test]
async fn list_public_groups_returns_all_public_and_excludes_private() {
    let fixture = Fixture::new().await;
    for bot in ["driver-a", "driver-b"] {
        fixture.add_public_bot(bot).await;
    }

    // driver-b 公开群；driver-a 私有群（caller 不是任何群的参与者）
    for (group_id, visibility, updated_at) in [
        ("pub-recent", "public", 30),
        ("priv-hidden", "private", 20),
        ("pub-older", "public", 10),
    ] {
        let mut group = normal_group(
            group_id,
            if group_id.starts_with("pub") { "driver-b" } else { "driver-a" },
            vec![Participant::bot(
                if group_id.starts_with("pub") { "driver-b" } else { "driver-a" },
                ParticipantRole::Driver,
            )],
            GroupStrategy::Chat,
            updated_at,
        );
        group.visibility = visibility.into();
        fixture.groups.upsert(group).await.expect("store Group");
    }

    let page = fixture
        .service
        .list_public_groups(ListPublicGroups {
            offset: 0,
            limit: 20,
            q: None,
            strategy: None,
        })
        .await
        .expect("list public groups");

    assert_eq!(page.total, 2);
    assert_eq!(page.items.len(), 2);
    for item in &page.items {
        match item {
            GroupSummary::Normal(summary) => {
                assert_eq!(summary.membership, Membership::None);
                assert!(summary.group_id.starts_with("pub"));
            }
            other => panic!("expected normal summary, got {other:?}"),
        }
    }
}
#[tokio::test]
async fn list_summaries_include_group_context() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "target"] {
        fixture.add_public_bot(bot).await;
    }
    let mut group = normal_group(
        "ctx-group",
        "driver",
        vec![
            Participant::bot("driver", ParticipantRole::Driver),
            Participant::bot("target", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        10,
    );
    group.visibility = "public".into();
    group.context = Some("plaza context".to_string());
    fixture.groups.upsert(group).await.expect("store Group");

    let public_page = fixture
        .service
        .list_public_groups(ListPublicGroups {
            offset: 0,
            limit: 10,
            q: None,
            strategy: None,
        })
        .await
        .expect("list public groups");
    assert_eq!(public_page.items.len(), 1);
    match &public_page.items[0] {
        GroupSummary::Normal(summary) => {
            assert_eq!(summary.context.as_deref(), Some("plaza context"));
        }
        other => panic!("expected normal summary, got {other:?}"),
    }

    let page = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("target"),
            view_bot_id: Some("target".into()),
            offset: 0,
            limit: 10,
            q: None,
            visibility: None,
            membership: MembershipFilter::Direct,
            kind: GroupKindFilter::All,
            strategy: None,
        })
        .await
        .expect("list groups");
    assert_eq!(page.items.len(), 1);
    match &page.items[0] {
        GroupSummary::Normal(summary) => {
            assert_eq!(summary.context.as_deref(), Some("plaza context"));
        }
        other => panic!("expected normal summary, got {other:?}"),
    }
}
#[tokio::test]
async fn list_public_groups_filters_by_strategy() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;

    for (group_id, strategy) in [
        ("chat-group", GroupStrategy::Chat),
        ("sm-group", GroupStrategy::StateMachine),
    ] {
        let mut group = normal_group(
            group_id,
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
            strategy,
            10,
        );
        group.visibility = "public".into();
        fixture.groups.upsert(group).await.expect("store Group");
    }

    let page = fixture
        .service
        .list_public_groups(ListPublicGroups {
            offset: 0,
            limit: 20,
            q: None,
            strategy: Some(V1GroupStrategy::StateMachine),
        })
        .await
        .expect("list state_machine public groups");

    assert_eq!(page.total, 1);
    match &page.items[0] {
        GroupSummary::Normal(summary) => assert_eq!(summary.group_id, "sm-group"),
        other => panic!("expected normal summary, got {other:?}"),
    }
}
#[tokio::test]
async fn list_public_groups_paginates_and_reports_filtered_total() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;

    for idx in 0..5 {
        let mut group = normal_group(
            &format!("pub-{idx}"),
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
            GroupStrategy::Chat,
            10 + idx,
        );
        group.visibility = "public".into();
        fixture.groups.upsert(group).await.expect("store Group");
    }

    let page = fixture
        .service
        .list_public_groups(ListPublicGroups {
            offset: 1,
            limit: 2,
            q: None,
            strategy: None,
        })
        .await
        .expect("paginated public groups");

    assert_eq!(page.total, 5);
    assert_eq!(page.items.len(), 2);
    assert_eq!(page.offset, 1);
    assert_eq!(page.limit, 2);
}
#[tokio::test]
async fn list_public_groups_filters_by_label_query() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;

    for group_id in ["plaza-alpha", "plaza-beta", "other-gamma"] {
        let mut group = normal_group(
            group_id,
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
            GroupStrategy::Chat,
            10,
        );
        group.visibility = "public".into();
        fixture.groups.upsert(group).await.expect("store Group");
    }

    let page = fixture
        .service
        .list_public_groups(ListPublicGroups {
            offset: 0,
            limit: 20,
            q: Some("plaza".into()),
            strategy: None,
        })
        .await
        .expect("label-filtered public groups");

    assert_eq!(page.total, 2);
    let mut ids: Vec<_> = page
        .items
        .iter()
        .map(|item| match item {
            GroupSummary::Normal(s) => s.group_id.clone(),
            other => panic!("expected normal, got {other:?}"),
        })
        .collect();
    ids.sort();
    assert_eq!(ids, vec!["plaza-alpha", "plaza-beta"]);
}
#[tokio::test]
async fn list_public_groups_excludes_inactive_records() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;

    for (group_id, status) in [("active-one", "active"), ("deleted-one", "deleted")] {
        let mut group = normal_group(
            group_id,
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
            GroupStrategy::Chat,
            10,
        );
        group.visibility = "public".into();
        group.record_status = status.into();
        fixture.groups.upsert(group).await.expect("store Group");
    }

    let page = fixture
        .service
        .list_public_groups(ListPublicGroups {
            offset: 0,
            limit: 20,
            q: None,
            strategy: None,
        })
        .await
        .expect("active-only public groups");

    assert_eq!(page.total, 1);
    match &page.items[0] {
        GroupSummary::Normal(summary) => assert_eq!(summary.group_id, "active-one"),
        other => panic!("expected normal summary, got {other:?}"),
    }
}
#[tokio::test]
async fn list_public_groups_rejects_out_of_range_limit() {
    let fixture = Fixture::new().await;

    for invalid_limit in [0u64, 101] {
        let result = fixture
            .service
            .list_public_groups(ListPublicGroups {
                offset: 0,
                limit: invalid_limit,
                q: None,
                strategy: None,
            })
            .await;
        assert!(result.is_err(), "limit {invalid_limit} should be rejected");
        let err = result.unwrap_err();
        assert!(
            matches!(err, ApplicationError::InvalidInput { .. }),
            "expected InvalidInput for limit {invalid_limit}, got {err:?}"
        );
    }
}
