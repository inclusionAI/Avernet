//! Repository contract harnesses.
//!
//! Concrete repository implementations call these functions from
//! `tests/conformance_*.rs`.

use bcs_domain::{
    MessageOwnerFilter, MessageQuery, NewMessage, SenderType,
};
use bcs_service_api::{
    BindingChannel, BotCapabilities, BotRepoPort, FriendRepoPort, FriendRequest,
    FriendRequestDirection, FriendRequestRepoPort, FriendRequestStatus, Group, GroupChatProposal,
    GroupKind, GroupRepoPort, GroupStatus, NewSessionParams, Participant,
    ParticipantMode, ParticipantRole, ProposalCoreService, RelationEdge, RelationRepoPort,
    ServiceSpec, Session, SessionKind, SessionRepoPort, SessionStatus, Skill,
};
use bcs_service_api::ServiceError;
use bcs_service_api::port::repo::{
    CreateOrganizationRecord, ListOrganizationMembersQuery, MessageRepoPort,
    OrganizationRepoPort, UpsertOrganizationMemberRecord,
};

pub async fn organization_repo_contract_tests<T: OrganizationRepoPort + ?Sized>(repo: &T) {
    let created = repo
        .create_organization(CreateOrganizationRecord {
            env: "contract".to_string(),
            code: "promo-2026".to_string(),
            name: "Promo 2026".to_string(),
            description: Some("contract organization".to_string()),
            managing_provider_id: "provider-a".to_string(),
        })
        .await
        .expect("create organization");
    assert!(!created.disabled);

    let duplicate = repo
        .create_organization(CreateOrganizationRecord {
            env: "contract".to_string(),
            code: "promo-2026".to_string(),
            name: "Duplicate".to_string(),
            description: None,
            managing_provider_id: "provider-a".to_string(),
        })
        .await;
    assert!(matches!(duplicate, Err(ServiceError::Conflict(_))));

    let member = repo
        .upsert_member(UpsertOrganizationMemberRecord {
            env: "contract".to_string(),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-b".to_string(),
            role: Some("traffic_analyst".to_string()),
        })
        .await
        .expect("upsert member");
    assert!(!member.disabled);

    repo.set_member_disabled("contract", "promo-2026", "bot-b", true)
        .await
        .expect("disable member");
    assert!(repo
        .list_members(ListOrganizationMembersQuery {
            env: "contract".to_string(),
            organization_code: "promo-2026".to_string(),
            include_disabled: false,
            role: None,
        })
        .await
        .expect("list active members")
        .is_empty());

    let restored = repo
        .upsert_member(UpsertOrganizationMemberRecord {
            env: "contract".to_string(),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-b".to_string(),
            role: Some("merchant_growth".to_string()),
        })
        .await
        .expect("restore member");
    assert_eq!(restored.role.as_deref(), Some("merchant_growth"));
    assert!(!restored.disabled);
}

pub async fn bot_repo_contract_tests<T: BotRepoPort + ?Sized>(repo: &T) {
    let bot_id = "repo-contract-bot";
    let token = "repo-contract-token";
    let owner = "repo-owner";

    assert!(repo.get("bcs-contract-missing-bot").await.is_none());

    let mut binding_channels = std::collections::HashMap::new();
    binding_channels.insert(
        "antding".to_string(),
        BindingChannel {
            binding_key: "repo-binding".to_string(),
        },
    );
    let caps = BotCapabilities {
        name: Some("Repo Contract Bot".to_string()),
        summary: Some("contract summary".to_string()),
        domains: vec!["contracts".to_string()],
        skills: vec![Skill::new("repo_contract")],
        visibility: "public".to_string(),
        binding_channels: Some(binding_channels),
        ..Default::default()
    };

    repo.register(bot_id.to_string(), caps)
        .await
        .expect("register");
    let stored = repo.get(bot_id).await.expect("registered bot");
    assert_eq!(stored.bot_uuid, bot_id);
    assert_eq!(
        stored.capabilities.name.as_deref(),
        Some("Repo Contract Bot")
    );
    assert_eq!(stored.capabilities.visibility, "public");

    assert_eq!(
        repo.get_by_ids(&[bot_id.to_string(), bot_id.to_string()])
            .await
            .len(),
        1
    );
    assert!(
        repo.list_active()
            .await
            .iter()
            .any(|bot| bot.bot_uuid == bot_id)
    );

    assert_eq!(
        repo.find_bot_by_binding_channel("antding", "repo-binding")
            .await
            .as_deref(),
        Some(bot_id)
    );

    repo.update_visibility(bot_id, "protected")
        .await
        .expect("update visibility");
    assert_eq!(
        repo.get(bot_id)
            .await
            .expect("bot after visibility update")
            .capabilities
            .visibility,
        "protected"
    );

    repo.save_created_by(bot_id, owner, true)
        .await
        .expect("save owner");
    assert!(
        repo.list_bots_by_creator(owner)
            .await
            .iter()
            .any(|bot| bot.bot_uuid == bot_id)
    );

    repo.save_token(bot_id, token).await.expect("save token");
    assert_eq!(repo.load_token(bot_id).await.as_deref(), Some(token));
    assert_eq!(repo.find_bot_by_token(token).await.as_deref(), Some(bot_id));
}

pub async fn bot_repo_port_contract_tests<T: BotRepoPort + ?Sized>(repo: &T) {
    bot_repo_contract_tests(repo).await;
}

pub async fn group_repo_contract_tests<T: GroupRepoPort + ?Sized>(repo: &T) {
    assert!(repo.get("repo-contract-missing-group").await.is_none());
    assert_eq!(repo.count().await, 0);

    let mut group = Group::new(
        "repo-contract-group",
        "repo-driver",
        vec![
            Participant::bot("repo-driver", ParticipantRole::Driver),
            Participant::bot("repo-helper", ParticipantRole::Consultant),
        ],
    );
    group.label = Some("initial label".to_string());

    repo.upsert(group.clone()).await.expect("upsert group");
    let stored = repo
        .get(&group.id)
        .await
        .expect("group exists after upsert");
    assert_eq!(stored.label.as_deref(), Some("initial label"));
    assert_eq!(stored.participants.len(), 2);
    assert_eq!(repo.count().await, 1);

    repo.add_participant(
        &group.id,
        Participant::bot("repo-observer", ParticipantRole::Observer),
    )
    .await
    .expect("add participant");
    let stored = repo.get(&group.id).await.expect("group after add");
    assert!(
        stored
            .participants
            .iter()
            .any(|p| p.bot_uuid == "repo-observer")
    );

    repo.update_participant_mode(&group.id, "repo-helper", ParticipantMode::Muted)
        .await
        .expect("update participant mode");
    let stored = repo.get(&group.id).await.expect("group after mode update");
    let helper = stored
        .participants
        .iter()
        .find(|p| p.bot_uuid == "repo-helper")
        .expect("helper participant");
    assert_eq!(helper.mode, Some(ParticipantMode::Muted));

    repo.update_label(&group.id, Some("updated label".to_string()))
        .await
        .expect("update label");
    assert_eq!(
        repo.get(&group.id).await.expect("group after label").label,
        Some("updated label".to_string())
    );

    repo.update_status(&group.id, GroupStatus::Completed)
        .await
        .expect("update status");
    assert_eq!(
        repo.get(&group.id)
            .await
            .expect("group after status")
            .status,
        GroupStatus::Completed
    );

    assert!(repo.list().await.iter().any(|listed| listed.id == group.id));
    assert!(
        repo.list_paginated(0, 10)
            .await
            .iter()
            .any(|listed| listed.id == group.id)
    );
    assert_eq!(repo.count_by_participant("repo-helper").await, 1);
    assert!(
        repo.find_by_participant("repo-helper")
            .await
            .iter()
            .any(|listed| listed.id == group.id)
    );
    assert!(
        repo.find_by_participant_paginated("repo-helper", 0, 10)
            .await
            .iter()
            .any(|listed| listed.id == group.id)
    );

    assert_eq!(
        repo.message_count(&group.id).await.expect("message count"),
        0
    );
    repo.increment_message_count(&group.id)
        .await
        .expect("increment message count");
    assert_eq!(
        repo.message_count(&group.id).await.expect("message count"),
        1
    );
    repo.reset_message_count(&group.id)
        .await
        .expect("reset message count");
    assert_eq!(
        repo.message_count(&group.id).await.expect("message count"),
        0
    );

    let pair_key = Group::compute_dm_pair_key("repo-dm-a", "repo-dm-b");
    let mut dm_group = Group::new(
        "repo-contract-dm",
        "repo-dm-a",
        vec![
            Participant::bot("repo-dm-a", ParticipantRole::Driver),
            Participant::bot("repo-dm-b", ParticipantRole::Consultant),
        ],
    );
    dm_group.group_kind = GroupKind::Dm;
    dm_group.dm_pair_key = Some(pair_key.clone());

    assert!(
        repo.insert_dm_group_if_absent(dm_group.clone())
            .await
            .expect("insert dm group")
    );

    let mut duplicate_dm = dm_group;
    duplicate_dm.id = "repo-contract-dm-duplicate".to_string();
    assert!(
        !repo
            .insert_dm_group_if_absent(duplicate_dm)
            .await
            .expect("reuse dm group")
    );
    assert_eq!(
        repo.find_dm_by_pair_key(&pair_key)
            .await
            .expect("find dm group")
            .id,
        "repo-contract-dm"
    );

    assert!(
        repo.delete(&group.id)
            .await
            .expect("delete group")
            .is_some()
    );
    assert!(repo.get(&group.id).await.is_none());

    // service_spec / version / record_status roundtrip (Task 10)
    // Re-upsert the group (was deleted above) with service_spec populated.
    let mut g = group.clone();
    g.service_spec = Some(ServiceSpec {
        callback_config: None,
        timeout_seconds: Some(60),
        max_concurrency: Some(8),
    });
    g.version = 1;
    g.record_status = "active".to_string();
    repo.upsert(g.clone()).await.expect("upsert service_spec");
    let fetched = repo
        .get(&g.id)
        .await
        .expect("get after service_spec upsert");
    let spec = fetched
        .service_spec
        .expect("service_spec should roundtrip");
    assert_eq!(spec.timeout_seconds, Some(60));
    assert_eq!(spec.max_concurrency, Some(8));
    assert!(spec.callback_config.is_none());
    assert_eq!(fetched.version, 1);
    assert_eq!(fetched.record_status, "active");
}

pub async fn group_repo_port_contract_tests<T: GroupRepoPort + ?Sized>(repo: &T) {
    group_repo_contract_tests(repo).await;
}

pub async fn friend_repo_contract_tests<T: FriendRepoPort + ?Sized>(repo: &T) {
    repo.add_friendship("repo-alice", "repo-bob")
        .await
        .expect("add friendship");
    assert!(
        repo.are_friends("repo-alice", "repo-bob")
            .await
            .expect("are friends")
    );
    assert!(
        repo.are_friends("repo-bob", "repo-alice")
            .await
            .expect("are friends reverse")
    );
    assert_eq!(
        repo.list_friends("repo-alice").await.expect("list friends"),
        vec!["repo-bob".to_string()]
    );

    assert_eq!(
        repo.remove_all_friendships("repo-alice")
            .await
            .expect("remove friendships"),
        1
    );
    assert!(
        !repo
            .are_friends("repo-alice", "repo-bob")
            .await
            .expect("are friends after remove")
    );
}

pub async fn friend_repo_port_contract_tests<T: FriendRepoPort + ?Sized>(repo: &T) {
    friend_repo_contract_tests(repo).await;
}

pub async fn friend_request_repo_contract_tests<T: FriendRequestRepoPort + ?Sized>(repo: &T) {
    let missing = repo
        .get_request("repo-request-missing")
        .await
        .expect_err("missing request should error");
    assert!(matches!(missing, ServiceError::FriendRequestNotFound(_)));

    let request = FriendRequest {
        id: "repo-request-1".to_string(),
        from_bot: "repo-alice".to_string(),
        to_bot: "repo-bob".to_string(),
        status: FriendRequestStatus::Pending,
        created_at: now_ms(),
        updated_at: now_ms(),
    };
    assert!(
        repo.insert_pending_request_if_absent(request.clone())
            .await
            .expect("insert pending request")
            .is_none()
    );
    assert_eq!(
        repo.insert_pending_request_if_absent(FriendRequest {
            id: "repo-request-duplicate".to_string(),
            from_bot: request.from_bot.clone(),
            to_bot: request.to_bot.clone(),
            status: FriendRequestStatus::Pending,
            created_at: now_ms(),
            updated_at: now_ms(),
        })
        .await
        .expect("duplicate pending request")
        .map(|found| found.id),
        Some(request.id.clone())
    );

    assert_eq!(
        repo.get_request(&request.id)
            .await
            .expect("get inserted request")
            .status,
        FriendRequestStatus::Pending
    );
    assert_eq!(
        repo.find_pending_request("repo-alice", "repo-bob")
            .await
            .expect("find pending")
            .map(|found| found.id),
        Some(request.id.clone())
    );
    assert!(
        repo.list_requests(
            "repo-alice",
            FriendRequestDirection::Sent,
            Some(FriendRequestStatus::Pending),
        )
        .await
        .iter()
        .any(|listed| listed.id == request.id)
    );

    repo.update_request_status(&request.id, FriendRequestStatus::Accepted)
        .await
        .expect("accept request");
    assert_eq!(
        repo.get_request(&request.id)
            .await
            .expect("get accepted request")
            .status,
        FriendRequestStatus::Accepted
    );

    let reverse = FriendRequest {
        id: "repo-request-reverse".to_string(),
        from_bot: "repo-bob".to_string(),
        to_bot: "repo-alice".to_string(),
        status: FriendRequestStatus::Pending,
        created_at: now_ms(),
        updated_at: now_ms(),
    };
    repo.insert_request(reverse.clone())
        .await
        .expect("insert reverse request");
    assert_eq!(
        repo.accept_reverse_pending_requests("repo-alice", "repo-bob")
            .await
            .expect("accept reverse"),
        1
    );
    assert_eq!(
        repo.get_request(&reverse.id)
            .await
            .expect("get reverse request")
            .status,
        FriendRequestStatus::Accepted
    );

    let pending = FriendRequest {
        id: "repo-request-pending-cancel".to_string(),
        from_bot: "repo-charlie".to_string(),
        to_bot: "repo-alice".to_string(),
        status: FriendRequestStatus::Pending,
        created_at: now_ms(),
        updated_at: now_ms(),
    };
    repo.insert_request(pending.clone())
        .await
        .expect("insert pending request");
    assert_eq!(
        repo.delete_pending_requests_for_bot("repo-alice")
            .await
            .expect("delete pending"),
        1
    );
    let missing_after_delete = repo
        .get_request(&pending.id)
        .await
        .expect_err("deleted pending request should be missing");
    assert!(matches!(
        missing_after_delete,
        ServiceError::FriendRequestNotFound(_)
    ));
}

pub async fn friend_request_repo_port_contract_tests<T: FriendRequestRepoPort + ?Sized>(repo: &T) {
    friend_request_repo_contract_tests(repo).await;
}

pub async fn proposal_repo_contract_tests<T: ProposalCoreService + ?Sized>(repo: &T) {
    let proposal = GroupChatProposal {
        token: "repo-proposal-token".to_string(),
        driver_bot: "driver".to_string(),
        participants: vec!["driver".to_string(), "helper".to_string()],
        reason: "contract".to_string(),
        proposed_by: "driver".to_string(),
        member_intros: "driver/helper".to_string(),
        confirm_url: "https://example.invalid/confirm".to_string(),
        created_at: now_ms(),
    };

    assert_eq!(repo.store(proposal.clone()).await, proposal.token);
    assert_eq!(
        repo.get(&proposal.token).await.map(|stored| stored.reason),
        Some("contract".to_string())
    );
    assert!(repo.take(&proposal.token).await.is_some());
    assert!(repo.get(&proposal.token).await.is_none());
}

pub async fn relation_repo_contract_tests<T: RelationRepoPort + ?Sized>(repo: &T) {
    let edge = RelationEdge {
        from_id: "repo-human".to_string(),
        to_id: "repo-bot".to_string(),
        env: "repo-env".to_string(),
        kinds: 0,
        allow: 0,
        deny: 0,
        is_creator: true,
    };

    repo.upsert_edge(edge.clone()).await.expect("upsert edge");
    let stored = repo
        .get_edge(&edge.from_id, &edge.to_id, &edge.env)
        .await
        .expect("get edge")
        .expect("stored edge");
    assert!(stored.is_creator);

    repo.delete_edge(&edge.from_id, &edge.to_id, &edge.env)
        .await
        .expect("delete edge");
    assert!(
        repo.get_edge(&edge.from_id, &edge.to_id, &edge.env)
            .await
            .expect("get after delete")
            .is_none()
    );
}

pub async fn relation_repo_port_contract_tests<T: RelationRepoPort + ?Sized>(repo: &T) {
    relation_repo_contract_tests(repo).await;
}

pub async fn session_repo_contract_tests<T: SessionRepoPort + ?Sized>(repo: &T) {
    let group_id = "repo-contract-session-group";
    let participants = vec![Participant::bot("bot1", ParticipantRole::Driver)];

    // create — chat session, auto-generated id
    let s: Session = repo
        .create(
            group_id,
            NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: participants.clone(),
                ..Default::default()
            },
        )
        .await
        .expect("create chat session");
    assert!(s.id.starts_with("repo-contract-session-group:"));
    assert_eq!(s.status, SessionStatus::Running);

    // get / belongs_to_group
    let fetched = repo.get(&s.id).await.expect("get session");
    assert_eq!(fetched.id, s.id);
    assert!(repo.belongs_to_group(&s.id, group_id).await);
    assert!(!repo.belongs_to_group(&s.id, "other-group").await);

    // list_by_group / latest_running
    let listed = repo
        .list_by_group(group_id, Some(SessionStatus::Running), 0, 10, None, None)
        .await;
    assert_eq!(listed.len(), 1);
    let latest = repo.latest_running(group_id).await.expect("latest running");
    assert_eq!(latest.id, s.id);

    // complete_if_running — first call succeeds
    let completed = repo
        .complete_if_running(&s.id, Some(serde_json::json!({"ok": 1})), None)
        .await
        .expect("complete_if_running first");
    assert!(completed.is_some(), "first complete returns Some");
    assert_eq!(
        completed.expect("completed session").status,
        SessionStatus::Completed
    );

    // complete_if_running — second call is a no-op
    let again = repo
        .complete_if_running(&s.id, None, None)
        .await
        .expect("complete_if_running second");
    assert!(again.is_none(), "CAS short-circuits on already-completed");

    // service_invocation session starts with callback_status="pending"
    let svc: Session = repo
        .create(
            group_id,
            NewSessionParams {
                session_kind: SessionKind::ServiceInvocation,
                participants: participants.clone(),
                ..Default::default()
            },
        )
        .await
        .expect("create service_invocation session");
    assert_eq!(svc.callback_status.as_deref(), Some("pending"));
    repo.complete_if_running(&svc.id, None, None)
        .await
        .expect("complete svc session");

    // reactivate must fail while callback is still pending
    let r = repo.reactivate(&svc.id, None).await;
    assert!(r.is_err(), "reactivate must reject when callback pending");

    // write terminal callback status, then reactivate succeeds
    repo.update_callback_status(&svc.id, "succeeded")
        .await
        .expect("update_callback_status");
    let reacted = repo
        .reactivate(&svc.id, None)
        .await
        .expect("reactivate after terminal callback");
    assert_eq!(reacted.status, SessionStatus::Running);
    assert_eq!(reacted.activation_count, 2);

    // count_running_service / list_running_service
    assert_eq!(repo.count_running_service(group_id).await, 1);
    let svc_running = repo.list_running_service(0, 10).await;
    assert!(svc_running.iter().any(|s| s.id == svc.id));

    // add_participant / update_participant_mode / remove_participant
    let extra = Participant::bot("bot2", ParticipantRole::Consultant);
    let added = repo
        .add_participant(&svc.id, extra.clone())
        .await
        .expect("add_participant");
    assert_eq!(added.participants.len(), 2);
    let modded = repo
        .update_participant_mode(&svc.id, "bot2", ParticipantMode::Muted)
        .await
        .expect("update_participant_mode");
    let bot2 = modded
        .participants
        .iter()
        .find(|p| p.bot_uuid == "bot2")
        .expect("bot2 participant");
    assert_eq!(bot2.mode, Some(ParticipantMode::Muted));
    let removed = repo
        .remove_participant(&svc.id, "bot2")
        .await
        .expect("remove_participant");
    assert_eq!(removed.participants.len(), 1);

    // update_title
    let titled = repo
        .update_title(&svc.id, Some("hello".to_string()))
        .await
        .expect("update_title");
    assert_eq!(titled.session_title.as_deref(), Some("hello"));

    // list_group_ids_by_session_participant
    let groups = repo.list_group_ids_by_session_participant("bot1").await;
    assert!(groups.contains(&group_id.to_string()));
}

pub async fn session_repo_port_contract_tests<T: SessionRepoPort + ?Sized>(repo: &T) {
    session_repo_contract_tests(repo).await;
}

pub async fn message_repo_contract_tests<T: MessageRepoPort + ?Sized>(repo: &T) {
    let group_id = "contract-group";
    let session_id = "contract-group:abcd1234";

    // append_message
    let msg = NewMessage {
        group_id: group_id.to_string(),
        session_id: session_id.to_string(),
        sender_id: "bot-a".to_string(),
        sender_type: SenderType::Bot,
        message_type: "text".to_string(),
        content: serde_json::json!({"text": "hello world"}),
        client_msg_id: Some("client-msg-1".to_string()),
        owner_bot_id: None,
        created_at: 1000,
        run_id: String::new(),
    };
    let persisted = repo.append_message(msg).await.expect("append_message");
    assert_eq!(persisted.session_id, session_id);
    assert_eq!(persisted.session_seq, 1);
    assert_eq!(persisted.sender_id, "bot-a");
    assert!(!persisted.message_id.is_empty());

    // Idempotency
    let dup_msg = NewMessage {
        group_id: group_id.to_string(),
        session_id: session_id.to_string(),
        sender_id: "bot-a".to_string(),
        sender_type: SenderType::Bot,
        message_type: "text".to_string(),
        content: serde_json::json!({"text": "duplicate"}),
        client_msg_id: Some("client-msg-1".to_string()),
        owner_bot_id: None,
        created_at: 2000,
        run_id: String::new(),
    };
    let dup = repo.append_message(dup_msg).await.expect("idempotent append");
    assert_eq!(dup.message_id, persisted.message_id);
    assert_eq!(dup.session_seq, 1);

    // Append more messages
    for i in 0..5u64 {
        let m = NewMessage {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            sender_id: if i % 2 == 0 { "bot-a".to_string() } else { "bot-b".to_string() },
            sender_type: SenderType::Bot,
            message_type: if i < 3 { "text".to_string() } else { "system_event".to_string() },
            content: serde_json::json!({"seq": i}),
            client_msg_id: None,
            owner_bot_id: None,
            created_at: 2000 + i * 100,
            run_id: String::new(),
        };
        repo.append_message(m).await.expect("append");
    }

    // get_current_seq
    let seq = repo.get_current_seq(session_id).await.expect("get_current_seq");
    assert_eq!(seq, 6);

    // query_messages — pagination
    let page = repo
        .query_messages(MessageQuery {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            cursor: None,
            limit: 3,
            keyword: None,
            sender_id: None,
            message_type: None,
            owner_filter: MessageOwnerFilter::Any,
            time_range: None,
            visible_from_seq: None,
        })
        .await
        .expect("query_messages");
    assert_eq!(page.messages.len(), 3);
    assert!(page.has_more);
    assert!(page.next_cursor.is_some());

    // query_messages — cursor
    let page2 = repo
        .query_messages(MessageQuery {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            cursor: page.next_cursor,
            limit: 10,
            keyword: None,
            sender_id: None,
            message_type: None,
            owner_filter: MessageOwnerFilter::Any,
            time_range: None,
            visible_from_seq: None,
        })
        .await
        .expect("query_messages cursor");
    assert_eq!(page2.messages.len(), 3);
    assert!(!page2.has_more);

    // query_messages — sender filter
    let page3 = repo
        .query_messages(MessageQuery {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            cursor: None,
            limit: 10,
            keyword: None,
            sender_id: Some("bot-b".to_string()),
            message_type: None,
            owner_filter: MessageOwnerFilter::Any,
            time_range: None,
            visible_from_seq: None,
        })
        .await
        .expect("query by sender");
    assert!(page3.messages.iter().all(|m| m.sender_id == "bot-b"));

    // query_messages — message_type filter
    let page4 = repo
        .query_messages(MessageQuery {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            cursor: None,
            limit: 10,
            keyword: None,
            sender_id: None,
            message_type: Some("system_event".to_string()),
            owner_filter: MessageOwnerFilter::Any,
            time_range: None,
            visible_from_seq: None,
        })
        .await
        .expect("query by type");
    assert!(page4.messages.iter().all(|m| m.message_type == "system_event"));

    // query_messages — keyword search
    let page5 = repo
        .query_messages(MessageQuery {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            cursor: None,
            limit: 10,
            keyword: Some("hello".to_string()),
            sender_id: None,
            message_type: None,
            owner_filter: MessageOwnerFilter::Any,
            time_range: None,
            visible_from_seq: None,
        })
        .await
        .expect("keyword search");
    assert_eq!(page5.messages.len(), 1);

    // query_messages — visible_from_seq
    let page6 = repo
        .query_messages(MessageQuery {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            cursor: None,
            limit: 10,
            keyword: None,
            sender_id: None,
            message_type: None,
            owner_filter: MessageOwnerFilter::Any,
            time_range: None,
            visible_from_seq: Some(4),
        })
        .await
        .expect("visible_from_seq");
    assert!(page6.messages.iter().all(|m| m.session_seq >= 4));

    // get_message_by_id
    let found = repo
        .get_message_by_id(session_id, &persisted.message_id)
        .await
        .expect("get_message_by_id");
    assert!(found.is_some());
    assert_eq!(found.unwrap().message_id, persisted.message_id);

    // get_message_by_id — missing
    let missing = repo
        .get_message_by_id(session_id, "nonexistent")
        .await
        .expect("get_message_by_id missing");
    assert!(missing.is_none());

    // Empty session
    let empty_seq = repo.get_current_seq("empty-session").await.expect("empty seq");
    assert_eq!(empty_seq, 0);
    let empty_page = repo
        .query_messages(MessageQuery {
            group_id: "empty".to_string(),
            session_id: "empty-session".to_string(),
            cursor: None,
            limit: 10,
            keyword: None,
            sender_id: None,
            message_type: None,
            owner_filter: MessageOwnerFilter::Any,
            time_range: None,
            visible_from_seq: None,
        })
        .await
        .expect("empty query");
    assert!(empty_page.messages.is_empty());
    assert!(!empty_page.has_more);

    // owner_bot_id round-trip and filtering
    let mgr = repo
        .append_message(NewMessage {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            sender_id: "mgr".to_string(),
            sender_type: SenderType::Bot,
            message_type: "text".to_string(),
            content: serde_json::json!({"text": "manager"}),
            client_msg_id: None,
            owner_bot_id: Some("mgr".to_string()),
            created_at: 5000,
            run_id: String::new(),
        })
        .await
        .expect("append owner manager message");
    let worker_a = repo
        .append_message(NewMessage {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            sender_id: "workerA".to_string(),
            sender_type: SenderType::Bot,
            message_type: "text".to_string(),
            content: serde_json::json!({"text": "workerA"}),
            client_msg_id: None,
            owner_bot_id: Some("workerA".to_string()),
            created_at: 5100,
            run_id: String::new(),
        })
        .await
        .expect("append owner worker message");
    let sys = repo
        .append_message(NewMessage {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            sender_id: "system".to_string(),
            sender_type: SenderType::System,
            message_type: "system_event".to_string(),
            content: serde_json::json!({"text": "system"}),
            client_msg_id: None,
            owner_bot_id: None,
            created_at: 5200,
            run_id: String::new(),
        })
        .await
        .expect("append system ownerless message");
    assert_eq!(mgr.owner_bot_id.as_deref(), Some("mgr"));
    assert_eq!(worker_a.owner_bot_id.as_deref(), Some("workerA"));
    assert_eq!(sys.owner_bot_id, None);

    let owner_page = repo
        .query_messages(MessageQuery {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            cursor: None,
            limit: 10,
            keyword: None,
            sender_id: None,
            message_type: None,
            owner_filter: MessageOwnerFilter::Eq("workerA".to_string()),
            time_range: Some((5000, 5200)),
            visible_from_seq: None,
        })
        .await
        .expect("query by owner_bot_id");
    assert_eq!(owner_page.messages.len(), 1);
    assert_eq!(owner_page.messages[0].owner_bot_id.as_deref(), Some("workerA"));

    let unfiltered_owner_page = repo
        .query_messages(MessageQuery {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            cursor: None,
            limit: 10,
            keyword: None,
            sender_id: None,
            message_type: None,
            owner_filter: MessageOwnerFilter::Any,
            time_range: Some((5000, 5200)),
            visible_from_seq: None,
        })
        .await
        .expect("query without owner filter");
    assert_eq!(unfiltered_owner_page.messages.len(), 3);

    let public_owner_page = repo
        .query_messages(MessageQuery {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            cursor: None,
            limit: 10,
            keyword: None,
            sender_id: None,
            message_type: None,
            owner_filter: MessageOwnerFilter::IsNull,
            time_range: Some((5000, 5200)),
            visible_from_seq: None,
        })
        .await
        .expect("query public owner rows");
    assert_eq!(public_owner_page.messages.len(), 1);
    assert_eq!(public_owner_page.messages[0].owner_bot_id, None);
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}
