use bcs_service_api::port::repo::{
    AuthzDecisionLogRepoPort, BotControlPlaneRecord, BotControlPlaneRepoPort, BotRepoPort,
    CapabilityCatalogRepoPort, EdgeGrantRepoPort, FriendRepoPort, FriendRequestRepoPort,
    GroupRepoPort, OrganizationRepoPort, PermissionProfileRepoPort, PermissionRequestRepoPort,
    RelationRepoPort,
};

fn assert_repo_traits_are_object_safe(
    _bot: Option<&dyn BotRepoPort>,
    _bot_control_plane: Option<&dyn BotControlPlaneRepoPort>,
    _group: Option<&dyn GroupRepoPort>,
    _friend: Option<&dyn FriendRepoPort>,
    _friend_request: Option<&dyn FriendRequestRepoPort>,
    _relation: Option<&dyn RelationRepoPort>,
    _organization: Option<&dyn OrganizationRepoPort>,
    _capabilities: Option<&dyn CapabilityCatalogRepoPort>,
    _permission_profiles: Option<&dyn PermissionProfileRepoPort>,
    _edge_grants: Option<&dyn EdgeGrantRepoPort>,
    _permission_requests: Option<&dyn PermissionRequestRepoPort>,
    _authz_decision_logs: Option<&dyn AuthzDecisionLogRepoPort>,
) {
}

#[test]
fn repo_traits_are_exposed_under_port_repo() {
    assert_repo_traits_are_object_safe(None, None, None, None, None, None, None, None, None, None, None, None);
    let _legacy_record_path: Option<BotControlPlaneRecord> = None;
}
