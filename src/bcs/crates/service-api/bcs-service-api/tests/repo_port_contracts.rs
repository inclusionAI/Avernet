use bcs_service_api::port::repo::{
    BotRepoPort, FriendRepoPort, FriendRequestRepoPort, GroupRepoPort,
    OrganizationRepoPort, RelationRepoPort,
};

fn assert_repo_traits_are_object_safe(
    _bot: Option<&dyn BotRepoPort>,
    _group: Option<&dyn GroupRepoPort>,
    _friend: Option<&dyn FriendRepoPort>,
    _friend_request: Option<&dyn FriendRequestRepoPort>,
    _relation: Option<&dyn RelationRepoPort>,
    _organization: Option<&dyn OrganizationRepoPort>,
) {
}

#[test]
fn repo_traits_are_exposed_under_port_repo() {
    assert_repo_traits_are_object_safe(None, None, None, None, None, None);
}
