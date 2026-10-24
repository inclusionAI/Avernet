//! P0-frozen caller identity contract tests (spec
//! `2026-09-16-caller-identity-session-container` §10.2, §10.3, §10.7).
//!
//! These lock the persisted-safe data contract only: no trait behavior and no
//! Caller execution is enabled by this module. All types here must stay free
//! of Human identity material (no staff_no, tokens, cookies or redeemable
//! refs) — that boundary is enforced structurally by the module under test.

use bcs_domain::caller_identity::{
    BotCallerBlockedReason, BotCallerEffectiveStatus, CallerConfigRevisionDecision,
    CallerIdentityConfig, CallerIdentitySnapshot, CallerMinimumIsolation, CallerTokenVisibility,
    ProviderCallerCapabilities,
};

fn enabled_config() -> CallerIdentityConfig {
    CallerIdentityConfig {
        enabled: true,
        minimum_isolation: CallerMinimumIsolation::CallerContainer,
    }
}

fn enabled_snapshot(revision: u64) -> CallerIdentitySnapshot {
    CallerIdentitySnapshot {
        source_revision: revision,
        caller_identity: enabled_config(),
    }
}

fn provider_caller_caps(supports_caller: bool) -> ProviderCallerCapabilities {
    ProviderCallerCapabilities {
        container_routing: Default::default(),
        supports_caller,
        supports_isolation_binding: false,
        supports_observe_inject: false,
        token_visibility: CallerTokenVisibility::RuntimeOnly,
    }
}

#[test]
fn caller_identity_config_defaults_off_and_requires_explicit_enabled() {
    // Rust Default is "off" and is only a never-configured fallback; the wire
    // form still requires an explicit `enabled`.
    let default = CallerIdentityConfig::default();
    assert!(!default.enabled);
    assert_eq!(
        default.minimum_isolation,
        CallerMinimumIsolation::CallerContainer
    );

    assert!(serde_json::from_value::<CallerIdentityConfig>(serde_json::json!({
        "minimum_isolation": "caller_container"
    }))
    .is_err());
    // Scalar strings/coercible numbers are not a bool either.
    for body in [
        r#"{"enabled":1}"#,
        r#"{"enabled":"false"}"#,
        r#"{"enabled":null}"#,
    ] {
        assert!(
            serde_json::from_str::<CallerIdentityConfig>(body).is_err(),
            "must reject: {body}"
        );
    }
}

#[test]
fn omitted_minimum_isolation_is_equivalent_to_caller_container() {
    let omitted = serde_json::from_value::<CallerIdentityConfig>(serde_json::json!({
        "enabled": true
    }))
    .unwrap();
    let explicit = serde_json::from_value::<CallerIdentityConfig>(serde_json::json!({
        "enabled": true,
        "minimum_isolation": "caller_container"
    }))
    .unwrap();
    assert_eq!(omitted, explicit);
    assert_eq!(
        omitted.minimum_isolation,
        CallerMinimumIsolation::CallerContainer
    );
    assert_eq!(
        serde_json::to_string(&explicit).unwrap(),
        r#"{"enabled":true,"minimum_isolation":"caller_container"}"#
    );
}

#[test]
fn caller_identity_config_rejects_unknown_fields_and_unknown_isolation() {
    // The retired mode-family words are explicitly not accepted side by side.
    assert!(serde_json::from_str::<CallerIdentityConfig>(
        r#"{"enabled":true,"mode":"supported"}"#
    )
    .is_err());
    for value in [
        "\"thread_isolated\"",
        "\"container_bound\"",
        "\"gateway_managed\"",
        "null",
        "3",
    ] {
        let body = format!(r#"{{"enabled":true,"minimum_isolation":{}}}"#, value);
        assert!(
            serde_json::from_str::<CallerIdentityConfig>(&body).is_err(),
            "minimum_isolation={value} must be rejected"
        );
    }
}

#[test]
fn caller_identity_snapshot_requires_revision_and_full_config() {
    let snapshot = serde_json::from_value::<CallerIdentitySnapshot>(serde_json::json!({
        "source_revision": 42,
        "caller_identity": { "enabled": true }
    }))
    .unwrap();
    assert_eq!(snapshot.source_revision, 42);
    assert_eq!(snapshot.caller_identity, enabled_config());

    // Missing pieces are rejected: no revision-only or config-only shape and
    // no extra fields. Revision stays the authoritative monotonic value; zero
    // is a legal revision, not an "unconfigured" sentinel (unconfigured is
    // expressed by `Option::None`, not by a number).
    for body in [
        r#"{"caller_identity":{"enabled":false}}"#,
        r#"{"source_revision":1}"#,
        r#"{"source_revision":1,"caller_identity":{"enabled":false},"env":"prod"}"#,
    ] {
        assert!(
            serde_json::from_str::<CallerIdentitySnapshot>(body).is_err(),
            "must reject: {body}"
        );
    }
    assert_eq!(
        serde_json::from_value::<CallerIdentitySnapshot>(serde_json::json!({
            "source_revision": 0,
            "caller_identity": { "enabled": false }
        }))
        .unwrap()
        .source_revision,
        0
    );
}

#[test]
fn revision_comparison_follows_the_monotonic_matrix() {
    // Unconfigured -> apply.
    assert_eq!(
        enabled_snapshot(7).compare_revision(None).unwrap(),
        CallerConfigRevisionDecision::Apply
    );
    // Higher revision always applies — including explicit disable.
    let current = enabled_snapshot(7);
    assert_eq!(
        CallerIdentitySnapshot {
            source_revision: 8,
            caller_identity: CallerIdentityConfig::default(),
        }
        .compare_revision(Some(&current))
        .unwrap(),
        CallerConfigRevisionDecision::Apply
    );
    // Same revision, same content -> unchanged.
    assert_eq!(
        enabled_snapshot(7).compare_revision(Some(&current)).unwrap(),
        CallerConfigRevisionDecision::Unchanged
    );
    // Same revision, different content -> conflict carrying only the revision.
    let conflicting = CallerIdentitySnapshot {
        source_revision: 7,
        caller_identity: CallerIdentityConfig::default(),
    };
    let conflict = conflicting.compare_revision(Some(&current)).unwrap_err();
    assert_eq!(conflict.source_revision, 7);
    // Lower revision -> stale; the compared snapshot is never mutated.
    assert_eq!(
        enabled_snapshot(6).compare_revision(Some(&current)).unwrap(),
        CallerConfigRevisionDecision::Stale
    );
    assert_eq!(current.source_revision, 7);
    assert_eq!(current.caller_identity, enabled_config());
}

#[test]
fn provider_caller_capabilities_require_all_four_fields() {
    let full = serde_json::json!({
        "supports_caller": true,
        "supports_isolation_binding": false,
        "supports_observe_inject": true,
        "token_visibility": "runtime_only"
    });
    let caps = serde_json::from_value::<ProviderCallerCapabilities>(full).unwrap();
    assert!(caps.supports_caller);
    assert!(!caps.supports_isolation_binding);
    assert!(caps.supports_observe_inject);
    assert_eq!(caps.token_visibility, CallerTokenVisibility::RuntimeOnly);

    // There are no "default supported" values, and unknown fields are errors.
    for body in [
        r#"{"supports_isolation_binding":true,"supports_observe_inject":true,"token_visibility":"runtime_only"}"#,
        r#"{"supports_caller":true,"supports_isolation_binding":true,"token_visibility":"runtime_only"}"#,
        r#"{"supports_caller":true,"supports_isolation_binding":true,"supports_observe_inject":true}"#,
        r#"{"supports_caller":true,"supports_isolation_binding":true,"supports_observe_inject":true,"token_visibility":"plaintext"}"#,
        r#"{"supports_caller":true,"supports_isolation_binding":true,"supports_observe_inject":true,"token_visibility":"runtime_only","downlink_enabled":true}"#,
    ] {
        assert!(
            serde_json::from_str::<ProviderCallerCapabilities>(body).is_err(),
            "must reject: {body}"
        );
    }
}

#[test]
fn effective_status_merge_follows_spec_10_7_3() {
    use BotCallerEffectiveStatus::{Blocked, Disabled, Ready};

    // disabled wins regardless of everything else.
    let off = CallerIdentityConfig::default();
    assert_eq!(BotCallerEffectiveStatus::merge(&off, None, false), Disabled);
    assert_eq!(
        BotCallerEffectiveStatus::merge(&off, Some(&provider_caller_caps(true)), true),
        Disabled
    );

    // enabled without usable provider caller support stays blocked, never
    // owner-fallback ready.
    assert_eq!(
        BotCallerEffectiveStatus::merge(&enabled_config(), None, true),
        Blocked(BotCallerBlockedReason::ProviderCallerUnsupported)
    );
    assert_eq!(
        BotCallerEffectiveStatus::merge(
            &enabled_config(),
            Some(&provider_caller_caps(false)),
            true
        ),
        Blocked(BotCallerBlockedReason::ProviderCallerUnsupported)
    );

    // capabilities satisfied but the BCS-side execution chain (identity
    // plugin / gateway wiring) not ready -> blocked, not ready.
    assert_eq!(
        BotCallerEffectiveStatus::merge(&enabled_config(), Some(&provider_caller_caps(true)), false),
        Blocked(BotCallerBlockedReason::CallerExecutionUnavailable)
    );

    // ready; optional binding and observe support do not gate H2A readiness.
    assert_eq!(
        BotCallerEffectiveStatus::merge(&enabled_config(), Some(&provider_caller_caps(true)), true),
        Ready
    );
}
