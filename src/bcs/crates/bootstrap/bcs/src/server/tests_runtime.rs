//! Tests mod split out from server.rs: candidate_search_wiring_tests.

    use super::*;

use super::*;
    use bcs_test_support::{NoopBotRegistryCoreService, NoopFriendCoreService};

    #[test]
    fn bindings_share_one_core_between_legacy_and_v1() {
        let bindings = build_candidate_search_bindings(
            &BcsConfig::default(),
            Arc::new(NoopBotRegistryCoreService),
            Arc::new(NoopFriendCoreService),
            None,
        );

        assert!(Arc::ptr_eq(&bindings.legacy, &bindings.openapi_v1));
    }
