use super::*;

/// Real authority chain mirroring the fixture
/// `created_by` rows as live owner edges
/// (bot-authority plan Task 6.5; the
/// `created_by` reads themselves are gone).
pub(super) fn staff_authority(pairs: &[(&str, &str)]) -> Arc<LazySeedAuthorityHook> {
    let chain = bcs_test_support::lazy_authority_chain();
    for (bot_id, owner) in pairs {
        chain.hook.seed_owner(bot_id, owner);
    }
    chain.hook
}

pub(super) const DRIVER_OWNER_1: &[(&str, &str)] = &[("bot-driver", "1")];

pub(super) fn with_hook(
    flow: BcsMessageFlow,
    pairs: &[(&str, &str)],
) -> BcsMessageFlow {
    flow.with_bot_authority_hook(Some(staff_authority(pairs)))
}
