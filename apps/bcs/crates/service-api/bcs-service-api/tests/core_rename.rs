use bcs_service_api::core::{
    BotRegistryCoreService, FriendCoreService, FriendRequestCoreService, FusionCoreService,
    GroupCoreService, ProposalCoreService, RelationCoreService, RoutingCoreService,
};

fn _assert_core_trait_names_resolve() {
    fn _check<T: ?Sized>() {}

    _check::<dyn BotRegistryCoreService>();
    _check::<dyn GroupCoreService>();
    _check::<dyn RoutingCoreService>();
    _check::<dyn FusionCoreService>();
    _check::<dyn ProposalCoreService>();
    _check::<dyn FriendCoreService>();
    _check::<dyn FriendRequestCoreService>();
    _check::<dyn RelationCoreService>();
}

#[test]
fn core_service_names_are_public() {
    _assert_core_trait_names_resolve();
}
