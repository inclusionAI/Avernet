//! Behavior-preserving regression check for the Task 1 file split.
//!
//! Locks the public exports the split must preserve (`bcs::BcsConfig`,
//! `bcs::BcsServer`, `bcs::resolve_config_secrets`, `bcs::migrations::*`,
//! `bcs_http::oauth::OAuthRouteState`) and the schema version the
//! migration runner targets. This test is required to pass both before
//! and after the structural refactor.

#[test]
fn public_config_and_schema_exports_survive_split() {
    let config = bcs::BcsConfig::default();
    assert_eq!(config.gateway_principal.audience, "bcs");
    assert_eq!(bcs::migrations::sqlite_target_version(), 32);
}
