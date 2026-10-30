//! Caller configuration DTO contract tests (spec §10.7.2): strict wire
//! shapes shared by the Provider-scoped GET/PATCH sub-resource. Unknown
//! fields and credential smuggles are rejected; `enabled` is never
//! defaulted on the wire.

use bcs_protocol::http::provider::{
    CallerConfigSyncStatusDto, CallerIdentityConfigDto, CallerIdentitySnapshotDto,
    CallerMinimumIsolationDto, PatchProviderBotCallerConfigRequest,
    ProviderBotCallerConfigResponse, SyncProviderBotCallerConfigResponse,
};
use serde_json::json;

#[test]
fn omitted_minimum_isolation_defaults_to_caller_container() {
    let config: CallerIdentityConfigDto =
        serde_json::from_value(json!({ "enabled": true })).expect("config");
    assert_eq!(
        config,
        CallerIdentityConfigDto {
            enabled: true,
            minimum_isolation: CallerMinimumIsolationDto::CallerContainer,
        }
    );
    assert_eq!(
        serde_json::to_value(CallerIdentityConfigDto {
            enabled: false,
            minimum_isolation: CallerMinimumIsolationDto::CallerContainer,
        })
        .expect("serialize"),
        json!({ "enabled": false, "minimum_isolation": "caller_container" })
    );
}

#[test]
fn enabled_is_required_and_strictly_typed() {
    assert!(serde_json::from_value::<CallerIdentityConfigDto>(json!({
        "minimum_isolation": "caller_container"
    }))
    .is_err());
    for value in [json!(1), json!("true"), json!(null)] {
        assert!(
            serde_json::from_value::<CallerIdentityConfigDto>(json!({ "enabled": value })).is_err(),
            "enabled={value} must be rejected"
        );
    }
    // The legacy mode-family words are not a second writable switch, and
    // credential/material smuggles are simply unknown fields.
    for extra in ["mode", "cookie", "authorization", "bearer_token", "iam_token", "x_one_id"] {
        assert!(
            serde_json::from_value::<CallerIdentityConfigDto>(
                json!({ "enabled": true, extra: "value" })
            )
            .is_err(),
            "unknown field {extra} must be rejected"
        );
    }
}

#[test]
fn snapshot_requires_full_content_and_a_finite_revision() {
    let snapshot = json!({
        "source_revision": 42,
        "caller_identity": { "enabled": true }
    });
    let parsed: CallerIdentitySnapshotDto =
        serde_json::from_value(snapshot).expect("snapshot");
    assert_eq!(parsed.source_revision, 42);
    assert!(parsed.caller_identity.enabled);

    for body in [
        json!({ "caller_identity": { "enabled": false } }),
        json!({ "source_revision": 42 }),
        json!({ "source_revision": 42, "caller_identity": { "enabled": true }, "env": "prod" }),
        json!({ "source_revision": -1, "caller_identity": { "enabled": true } }),
        json!({ "source_revision": u64::MAX, "caller_identity": { "enabled": true }, "junk": 1 }),
    ] {
        assert!(
            serde_json::from_value::<CallerIdentitySnapshotDto>(body).is_err(),
            "invalid snapshot body must be rejected"
        );
    }
}

#[test]
fn get_response_distinguishes_null_from_disabled() {
    let none: ProviderBotCallerConfigResponse =
        serde_json::from_value(json!({ "snapshot": null })).expect("null snapshot");
    assert_eq!(none.snapshot, None);

    let some: ProviderBotCallerConfigResponse = serde_json::from_value(json!({
        "snapshot": { "source_revision": 7, "caller_identity": { "enabled": true } }
    }))
    .expect("snapshot");
    assert_eq!(some.snapshot.expect("present").source_revision, 7);

    assert!(serde_json::from_value::<ProviderBotCallerConfigResponse>(json!({
        "snapshot": { "source_revision": 7, "caller_identity": { "enabled": false }, "extra": 1 }
    }))
    .is_err());
}

#[test]
fn patch_request_and_sync_response_are_closed() {
    let request: PatchProviderBotCallerConfigRequest = serde_json::from_value(json!({
        "snapshot": { "source_revision": 3, "caller_identity": { "enabled": true } }
    }))
    .expect("request");
    assert!(serde_json::from_value::<PatchProviderBotCallerConfigRequest>(json!({
        "snapshot": { "source_revision": 3 },
        "cookie": " smuggled"
    }))
    .is_err());
    // A partial body without the snapshot wrapper is not accepted.
    assert!(serde_json::from_value::<PatchProviderBotCallerConfigRequest>(json!({
        "source_revision": 3
    }))
    .is_err());

    let response = SyncProviderBotCallerConfigResponse {
        snapshot: Some(request.snapshot),
        sync_status: CallerConfigSyncStatusDto::Applied,
    };
    assert_eq!(
        serde_json::to_value(&response).expect("serialize"),
        json!({
            "snapshot": { "source_revision": 3, "caller_identity": { "enabled": true, "minimum_isolation": "caller_container" } },
            "sync_status": "applied"
        })
    );
    assert_eq!(
        serde_json::to_value(CallerConfigSyncStatusDto::Stale).expect("serialize"),
        json!("stale")
    );
    assert!(serde_json::from_value::<SyncProviderBotCallerConfigResponse>(json!({
        "snapshot": null, "sync_status": "conflict"
    }))
    .is_err());
}
