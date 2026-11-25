use super::*;

pub(super) struct Fixture {
    pub(super) group: Arc<GroupStore>,
    registry: Arc<FakeRegistry>,
    friend: Arc<FakeFriendCoreService>,
    pub(super) relation: Arc<FakeRelationCoreService>,
    provider_downlink_bots: Arc<RwLock<HashSet<String>>>,
    /// The authority chain backing the control
    /// legs (rows 14/15/16): a LAZY-seed live hook
    /// whose owner edges the sync `with_bot`
    /// builder enqueues.
    authority: Arc<bcs_test_support::LazySeedAuthorityHook>,
}

impl Fixture {
    pub(super) fn new() -> Self {
        Self {
            group: Arc::new(GroupStore::new()),
            registry: Arc::new(FakeRegistry::default()),
            friend: Arc::new(FakeFriendCoreService::default()),
            relation: Arc::new(FakeRelationCoreService::default()),
            provider_downlink_bots: Arc::new(RwLock::new(HashSet::new())),
            authority: bcs_test_support::lazy_authority_chain().hook,
        }
    }

    pub(super) fn service_with_limits(
        &self,
        max_group_members: usize,
        max_groups_as_driver: usize,
        max_groups_as_member: usize,
    ) -> GroupManagement {
        self.service_with_limits_and_session(
            max_group_members,
            max_groups_as_driver,
            max_groups_as_member,
            Arc::new(StaticSessionManagement::new(test_session(
                "group-under-test:abcdef12",
                "group-under-test",
                Vec::new(),
            ))),
        )
    }

    pub(super) fn service_with_limits_and_session(
        &self,
        max_group_members: usize,
        max_groups_as_driver: usize,
        max_groups_as_member: usize,
        session_management: Arc<StaticSessionManagement>,
    ) -> GroupManagement {
        GroupManagement::new(
            self.group.clone(),
            self.registry.clone(),
            self.friend.clone(),
            self.relation.clone(),
            GroupConfig {
                max_group_members,
                max_groups_as_driver,
                max_groups_as_member,
                relation_env: "dev".to_string(),
            },
            session_management.clone(),
            Arc::new(NoopSystemMessageService),
        )
        .with_bot_authority(self.authority.clone())
        .with_bot_runtime(Arc::new(FakeBotRuntimeConnectionService {
            provider_downlink_bots: self.provider_downlink_bots.clone(),
        }))
        .with_session_authorization(session_management)
    }

    pub(super) fn service_with_system_message(
        &self,
        system_message: Arc<dyn SystemMessageService>,
    ) -> GroupManagement {
        let session_management = Arc::new(StaticSessionManagement::new(test_session(
            "group-under-test:abcdef12",
            "group-under-test",
            Vec::new(),
        )));
        GroupManagement::new(
            self.group.clone(),
            self.registry.clone(),
            self.friend.clone(),
            self.relation.clone(),
            GroupConfig {
                max_group_members: 5,
                max_groups_as_driver: 10,
                max_groups_as_member: 10,
                relation_env: "dev".to_string(),
            },
            session_management.clone(),
            system_message,
        )
        .with_bot_authority(self.authority.clone())
        .with_bot_runtime(Arc::new(FakeBotRuntimeConnectionService {
            provider_downlink_bots: self.provider_downlink_bots.clone(),
        }))
        .with_session_authorization(session_management)
    }

    pub(super) fn with_bot(
        self,
        bot_uuid: &str,
        name: &str,
        visibility: &str,
        created_by: Option<&str>,
    ) -> Self {
        self.registry
            .insert(bot(bot_uuid, name, visibility, created_by));
        // Rows 14/15/16: the CURRENT owner edge
        // replaces the historical `created_by`.
        if let Some(owner) = created_by {
            self.authority.seed_owner(bot_uuid, owner);
        }
        self
    }

    pub(super) fn with_human(self, actor_id: &str, name: &str) -> Self {
        self.registry.insert(human(actor_id, name));
        self
    }

    pub(super) fn with_provider_downlink_bot(
        self,
        bot_uuid: &str,
        name: &str,
        visibility: &str,
        created_by: Option<&str>,
    ) -> Self {
        self.registry
            .insert(bot(bot_uuid, name, visibility, created_by));
        if let Some(owner) = created_by {
            self.authority.seed_owner(bot_uuid, owner);
        }
        self.provider_downlink_bots
            .write()
            .unwrap()
            .insert(bot_uuid.to_string());
        self
    }

    pub(super) fn with_friendship(self, a: &str, b: &str) -> Self {
        self.friend.insert(a, b);
        self
    }
}
