use super::*;

// =========================================================================
// Structured Routing Tests (Tasks 11.x)
// =========================================================================

/// Simple registry that returns pre-configured bots for capability lookups.
struct TestBotRegistry {
    bots: std::collections::HashMap<String, RegisteredBot>,
}

impl TestBotRegistry {
    fn new() -> Self {
        Self {
            bots: std::collections::HashMap::new(),
        }
    }
    fn add(&mut self, id: &str, skills: Vec<&str>, domains: Vec<&str>) {
        self.bots.insert(
            id.to_string(),
            RegisteredBot {
                bot_uuid: id.to_string(),
                capabilities: BotCapabilities {
                    name: Some(id.to_string()),
                    summary: None,
                    skills: skills
                        .into_iter()
                        .map(bcs_service_api::Skill::new)
                        .collect(),
                    domains: domains.into_iter().map(|s| s.to_string()).collect(),
                    scopes: vec![],
                    binding_channels: None,
                    hidden: false,
                    visibility: "protected".to_string(),
                    agent_code: None,
                    agent_token: None,
                },
                env: None,
                created_by: None,
                actor_kind: bcs_service_api::ActorKind::default(),
                status: bcs_service_api::ActorStatus::default(),
            },
        );
    }
}

#[async_trait]
impl bcs_service_api::BotRegistryCoreService for TestBotRegistry {
    async fn register(
        &self,
        _: String,
        _: BotCapabilities,
    ) -> bcs_service_api::ServiceResult<()> {
        Ok(())
    }
    async fn update_status(&self, _: &str) -> bool {
        false
    }
    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.bots.get(bot_id).cloned()
    }
    async fn get_agent_credentials(
        &self,
        _bot_id: &str,
    ) -> Option<bcs_service_api::AgentCredentials> {
        None
    }
    async fn list_active(&self) -> Vec<RegisteredBot> {
        self.bots.values().cloned().collect()
    }
    async fn discover(&self, _: &str) -> Vec<RegisteredBot> {
        vec![]
    }
    async fn find_by_skills(&self, _: &[&str]) -> Vec<RegisteredBot> {
        vec![]
    }
    async fn find_by_domains(&self, _: &[&str]) -> Vec<RegisteredBot> {
        vec![]
    }
    async fn find_by_scopes(&self, _: &[&str]) -> Vec<RegisteredBot> {
        vec![]
    }
    async fn unregister(&self, _: &str) -> bool {
        false
    }
    async fn cleanup_expired(&self) {}
    async fn load_from_storage(&self, _: &str) -> Option<BotCapabilities> {
        None
    }
    async fn save_to_storage(
        &self,
        _: &str,
        _: &BotCapabilities,
    ) -> bcs_service_api::ServiceResult<()> {
        Ok(())
    }
    async fn update_visibility(&self, _: &str, _: &str) -> bcs_service_api::ServiceResult<()> {
        Ok(())
    }
    #[allow(deprecated)]
    async fn set_hidden(&self, _: &str, _: bool) -> bcs_service_api::ServiceResult<()> {
        Ok(())
    }
    async fn has_been_onboarded(&self, _: &str) -> bool {
        false
    }
    async fn save_token(&self, _: &str, _: &str) -> bcs_service_api::ServiceResult<()> {
        Ok(())
    }
    async fn load_token(&self, _: &str) -> Option<String> {
        None
    }
    async fn find_bot_by_token(&self, _: &str) -> Option<String> {
        None
    }
    async fn register_streaming_connection(&self, _: String) -> Result<String, ()> {
        Err(())
    }
    async fn reconnect_streaming(&self, _: String) -> Result<(String, String), ()> {
        Err(())
    }
    async fn disconnect_streaming(&self, _: &str) {}
    async fn is_connected(&self, _: &str) -> bool {
        false
    }
    async fn send_frame(&self, _: &str, _: String) -> Result<(), ()> {
        Err(())
    }
    async fn list_connected(&self) -> Vec<String> {
        vec![]
    }
    async fn store_token_mapping(&self, _: String, _: String) {}
    async fn register_http_connection(&self, _: String, token: String) -> String {
        token
    }
    async fn save_created_by(
        &self,
        _bot_id: &str,
        _created_by: &str,
        _overwrite: bool,
    ) -> bcs_service_api::ServiceResult<()> {
        Ok(())
    }
    async fn list_bots_by_creator(&self, _created_by: &str) -> Vec<RegisteredBot> {
        vec![]
    }

async fn list_uninitialized_bots_by_creator(
    &self,
    _created_by: &str,
    _limit: usize,
) -> bcs_service_api::ServiceResult<Vec<RegisteredBot>> {
    Ok(Vec::new())
}
}

fn create_structured_test_session() -> Group {
    Group {
        id: "grp-001".to_string(),
        label: None,
        status: bcs_service_api::GroupStatus::Active,
        driver_bot: "alice".to_string(),
        originator: Some("alice".to_string()),
        routing_policy: None,
        human_mention_notify_mode: HumanMentionNotifyMode::default(),
        context: None,
        opening_message: None,
        participants: vec![
            Participant {
                bot_uuid: "alice".to_string(),
                bot_name: Some("Alice".to_string()),
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: bcs_service_api::ActorKind::default(),
                tags: Vec::new(),
                message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
                mode: None,
            },
            Participant {
                bot_uuid: "bob".to_string(),
                bot_name: Some("Bob".to_string()),
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: bcs_service_api::ActorKind::default(),
                tags: Vec::new(),
                message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
                mode: None,
            },
            Participant {
                bot_uuid: "carol".to_string(),
                bot_name: Some("Carol".to_string()),
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: bcs_service_api::ActorKind::default(),
                tags: Vec::new(),
                message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
                mode: None,
            },
        ],
        messages: Vec::new(),
        service_group_uuid: None,
        service_mode: None,
        created_at: 0,
        updated_at: 0,
        group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
    }
}

// 11.1: RouteSelectorWire → RouteSelector conversion
#[test]
fn test_wire_to_selector_bot() {
    let wire = RouteSelectorWire {
        selector_type: "bot".to_string(),
        value: Some("abc".to_string()),
    };
    let sel = RouteSelector::try_from(&wire).unwrap();
    assert_eq!(
        sel,
        RouteSelector::Bot {
            bot_uuid: "abc".to_string()
        }
    );
}

#[test]
fn test_wire_to_selector_capability() {
    let wire = RouteSelectorWire {
        selector_type: "capability".to_string(),
        value: Some("database".to_string()),
    };
    let sel = RouteSelector::try_from(&wire).unwrap();
    assert_eq!(
        sel,
        RouteSelector::Capability {
            value: "database".to_string()
        }
    );
}

#[test]
fn test_wire_to_selector_participants_all() {
    let wire = RouteSelectorWire {
        selector_type: "participants".to_string(),
        value: Some("all".to_string()),
    };
    let sel = RouteSelector::try_from(&wire).unwrap();
    assert_eq!(sel, RouteSelector::ParticipantsAll);
}

#[test]
fn test_wire_to_selector_unknown_type_errors() {
    let wire = RouteSelectorWire {
        selector_type: "unknown".to_string(),
        value: None,
    };
    let err = RouteSelector::try_from(&wire).unwrap_err();
    assert!(matches!(err, RouteSelectorError::UnsupportedType(_)));
}

#[test]
fn test_wire_to_selector_missing_value_errors() {
    let wire = RouteSelectorWire {
        selector_type: "bot".to_string(),
        value: None,
    };
    let err = RouteSelector::try_from(&wire).unwrap_err();
    assert!(matches!(err, RouteSelectorError::MissingValue(_)));
}

#[tokio::test]
async fn test_bot_registry_update_status_rejects_heartbeat_renewal() {
    use bcs_service_api::BotRegistryCoreService as _;
    let registry = TestBotRegistry::new();
    assert!(!registry.update_status("heartbeat-renewal-unsupported").await);
}

// 11.2: Capability exact tag matching
#[tokio::test]
async fn test_capability_exact_match() {
    let router = MessageRouter::new();
    let session = create_structured_test_session();
    let mut registry = TestBotRegistry::new();
    registry.add("alice", vec![], vec![]);
    registry.add("bob", vec!["sql_analysis"], vec!["database"]);
    registry.add("carol", vec!["code_review"], vec!["security"]);

    let routing = ChatEventRouting {
        responders: vec![RouteSelectorWire {
            selector_type: "capability".to_string(),
            value: Some("database".to_string()),
        }],
        mode: Some(ResponseMode::Required),
        reason: "need db expert".to_string(),
        include_self: None,
        dedupe_key: None,
    };

    let decision = router
        .route_structured(&session, &routing, "alice", &registry)
        .await
        .unwrap();
    // Bob has "database" domain → Send; Alice (sender) excluded; Carol → Inject
    let bob = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "bob")
        .unwrap();
    let carol = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "carol")
        .unwrap();
    assert_eq!(bob.delivery_type, DeliveryType::Send);
    assert_eq!(carol.delivery_type, DeliveryType::Inject);
    assert!(decision.mentions.contains(&"bob".to_string()));
}

// 11.3: participants:all default excludes sender, include_self=true includes sender
#[tokio::test]
async fn test_participants_all_excludes_sender() {
    let router = MessageRouter::new();
    let session = create_structured_test_session();
    let registry = NoopBotRegistryCoreService;

    let routing = ChatEventRouting {
        responders: vec![RouteSelectorWire {
            selector_type: "participants".to_string(),
            value: Some("all".to_string()),
        }],
        mode: None,
        reason: "broadcast".to_string(),
        include_self: None, // default false
        dedupe_key: None,
    };

    let decision = router
        .route_structured(&session, &routing, "alice", &registry)
        .await
        .unwrap();
    // Alice (sender) excluded, Bob and Carol get Send
    assert_eq!(decision.targets.len(), 2);
    assert!(decision.targets.iter().all(|t| t.bot_uuid != "alice"));
    assert!(
        decision
            .targets
            .iter()
            .all(|t| t.delivery_type == DeliveryType::Send)
    );
}

#[tokio::test]
async fn test_participants_all_include_self() {
    let router = MessageRouter::new();
    let session = create_structured_test_session();
    let registry = NoopBotRegistryCoreService;

    let routing = ChatEventRouting {
        responders: vec![RouteSelectorWire {
            selector_type: "participants".to_string(),
            value: Some("all".to_string()),
        }],
        mode: None,
        reason: "broadcast including self".to_string(),
        include_self: Some(true),
        dedupe_key: None,
    };

    let decision = router
        .route_structured(&session, &routing, "alice", &registry)
        .await
        .unwrap();
    // All 3 participants get Send (including sender)
    assert_eq!(decision.targets.len(), 3);
    let alice = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "alice")
        .unwrap();
    assert_eq!(alice.delivery_type, DeliveryType::Send);
}

// 11.4: Multiple selectors OR/union
#[tokio::test]
async fn test_multiple_selectors_union() {
    let router = MessageRouter::new();
    let session = create_structured_test_session();
    let mut registry = TestBotRegistry::new();
    registry.add("alice", vec![], vec![]);
    registry.add("bob", vec![], vec!["database"]);
    registry.add("carol", vec![], vec!["security"]);

    let routing = ChatEventRouting {
        responders: vec![
            RouteSelectorWire {
                selector_type: "capability".to_string(),
                value: Some("database".to_string()),
            },
            RouteSelectorWire {
                selector_type: "capability".to_string(),
                value: Some("security".to_string()),
            },
        ],
        mode: None,
        reason: "need both".to_string(),
        include_self: None,
        dedupe_key: None,
    };

    let decision = router
        .route_structured(&session, &routing, "alice", &registry)
        .await
        .unwrap();
    // Both Bob (database) and Carol (security) get Send
    let bob = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "bob")
        .unwrap();
    let carol = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "carol")
        .unwrap();
    assert_eq!(bob.delivery_type, DeliveryType::Send);
    assert_eq!(carol.delivery_type, DeliveryType::Send);
}

// 11.5: Bot selector for non-participant returns TARGET_NOT_IN_GROUP
#[tokio::test]
async fn test_bot_not_in_group() {
    let router = MessageRouter::new();
    let session = create_structured_test_session();
    let registry = NoopBotRegistryCoreService;

    let routing = ChatEventRouting {
        responders: vec![RouteSelectorWire {
            selector_type: "bot".to_string(),
            value: Some("unknown_bot".to_string()),
        }],
        mode: None,
        reason: "test".to_string(),
        include_self: None,
        dedupe_key: None,
    };

    let err = router
        .route_structured(&session, &routing, "alice", &registry)
        .await
        .unwrap_err();
    assert!(matches!(
        err,
        bcs_service_api::StructuredRoutingError::TargetNotInGroup(_)
    ));
}

// 11.6: Name selector ambiguity
#[tokio::test]
async fn test_name_ambiguous() {
    let router = MessageRouter::new();
    let session = Group {
        id: "grp-002".to_string(),
        label: None,
        status: bcs_service_api::GroupStatus::Active,
        driver_bot: "a".to_string(),
        originator: None,
        routing_policy: None,
        human_mention_notify_mode: HumanMentionNotifyMode::default(),
        context: None,
        opening_message: None,
        participants: vec![
            Participant {
                bot_uuid: "a".to_string(),
                bot_name: Some("DBA".to_string()),
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: bcs_service_api::ActorKind::default(),
                tags: Vec::new(),
                message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
                mode: None,
            },
            Participant {
                bot_uuid: "b".to_string(),
                bot_name: Some("DBA".to_string()),
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: bcs_service_api::ActorKind::default(),
                tags: Vec::new(),
                message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
                mode: None,
            },
        ],
        messages: Vec::new(),
        service_group_uuid: None,
        service_mode: None,
        created_at: 0,
        updated_at: 0,
        group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
    };
    let registry = NoopBotRegistryCoreService;

    let routing = ChatEventRouting {
        responders: vec![RouteSelectorWire {
            selector_type: "name".to_string(),
            value: Some("DBA".to_string()),
        }],
        mode: None,
        reason: "test".to_string(),
        include_self: None,
        dedupe_key: None,
    };

    let err = router
        .route_structured(&session, &routing, "a", &registry)
        .await
        .unwrap_err();
    assert!(matches!(
        err,
        bcs_service_api::StructuredRoutingError::AmbiguousTarget(_)
    ));
}

// 11.7: No target matched
#[tokio::test]
async fn test_no_target_matched() {
    let router = MessageRouter::new();
    let session = create_structured_test_session();
    let registry = NoopBotRegistryCoreService;

    // Capability that nobody has (NoopRegistry returns None for all)
    let routing = ChatEventRouting {
        responders: vec![RouteSelectorWire {
            selector_type: "capability".to_string(),
            value: Some("quantum_computing".to_string()),
        }],
        mode: None,
        reason: "test".to_string(),
        include_self: None,
        dedupe_key: None,
    };

    let err = router
        .route_structured(&session, &routing, "alice", &registry)
        .await
        .unwrap_err();
    assert!(matches!(
        err,
        bcs_service_api::StructuredRoutingError::NoTargetMatched
    ));
}

// 11.7b: Driver and Originator selectors
#[tokio::test]
async fn test_driver_selector() {
    let router = MessageRouter::new();
    let session = create_structured_test_session();
    let registry = NoopBotRegistryCoreService;

    let routing = ChatEventRouting {
        responders: vec![RouteSelectorWire {
            selector_type: "driver".to_string(),
            value: None,
        }],
        mode: None,
        reason: "ask driver".to_string(),
        include_self: None,
        dedupe_key: None,
    };

    // Bob is sender, Alice is driver → Alice should get Send
    let decision = router
        .route_structured(&session, &routing, "bob", &registry)
        .await
        .unwrap();
    let alice = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "alice")
        .unwrap();
    assert_eq!(alice.delivery_type, DeliveryType::Send);
}

// 11.7c: Role selector
#[tokio::test]
async fn test_role_selector() {
    let router = MessageRouter::new();
    let session = create_structured_test_session();
    let registry = NoopBotRegistryCoreService;

    let routing = ChatEventRouting {
        responders: vec![RouteSelectorWire {
            selector_type: "role".to_string(),
            value: Some("consultant".to_string()),
        }],
        mode: None,
        reason: "ask consultants".to_string(),
        include_self: None,
        dedupe_key: None,
    };

    let decision = router
        .route_structured(&session, &routing, "alice", &registry)
        .await
        .unwrap();
    // Bob and Carol are consultants → Send; Alice (sender, driver) excluded
    assert_eq!(decision.targets.len(), 2);
    assert!(
        decision
            .targets
            .iter()
            .all(|t| t.delivery_type == DeliveryType::Send)
    );
}

// =========================================================================
// Task 4.2: build_sender_route_decision tests
// =========================================================================

#[test]
fn test_sender_route_all_targets_in_group() {
    let session = create_structured_test_session();
    let targets = vec!["bob".to_string(), "carol".to_string()];

    let decision = build_sender_route_decision(&session, "alice", &targets);

    // Sender (alice) excluded, bob and carol get Send
    assert_eq!(decision.targets.len(), 2);
    let bob = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "bob")
        .unwrap();
    let carol = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "carol")
        .unwrap();
    assert_eq!(bob.delivery_type, DeliveryType::Send);
    assert_eq!(carol.delivery_type, DeliveryType::Send);
}

#[test]
fn test_sender_route_some_targets_not_in_group() {
    let session = create_structured_test_session();
    let targets = vec!["bob".to_string(), "missing_bot".to_string()];

    let decision = build_sender_route_decision(&session, "alice", &targets);

    // bob gets Send, carol gets Inject, missing_bot skipped
    let bob = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "bob")
        .unwrap();
    let carol = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "carol")
        .unwrap();
    assert_eq!(bob.delivery_type, DeliveryType::Send);
    assert_eq!(carol.delivery_type, DeliveryType::Inject);
    assert!(decision.targets.iter().all(|t| t.bot_uuid != "missing_bot"));
}

#[test]
fn test_sender_route_empty_targets() {
    let session = create_structured_test_session();
    let targets: Vec<String> = vec![];

    let decision = build_sender_route_decision(&session, "alice", &targets);

    // All non-sender participants get Inject (no valid targets)
    assert_eq!(decision.targets.len(), 2);
    assert!(
        decision
            .targets
            .iter()
            .all(|t| t.delivery_type == DeliveryType::Inject)
    );
}

#[test]
fn test_sender_route_sender_excluded() {
    let session = create_structured_test_session();
    let targets = vec!["alice".to_string(), "bob".to_string()];

    let decision = build_sender_route_decision(&session, "alice", &targets);

    // alice is sender, excluded from targets
    assert!(decision.targets.iter().all(|t| t.bot_uuid != "alice"));
    let bob = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "bob")
        .unwrap();
    assert_eq!(bob.delivery_type, DeliveryType::Send);
}
