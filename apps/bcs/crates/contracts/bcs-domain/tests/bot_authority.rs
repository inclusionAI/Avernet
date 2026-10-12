//! Task 1 domain RED tests: role shapes, sources, and transfer states.
//!
//! Covers the Task 1 brief snippets verbatim plus:
//! - `BotAccessRelation` / `TransferStatus` serde round-trips and
//!   unknown-value rejection (SQL/JSON decode must never serde-default
//!   into a valid role);
//! - owner fixed `owner/owner` source encoding;
//! - empty team / transfer source IDs rejected (typed constructors and
//!   serde both reject);
//! - role edge fixed shape (`grant_ref_id = 0`, `rules = null`,
//!   `same_as_from`), non-role edges fixed `none/none` source encoding;
//! - runtime grant refs keep their original shape.

use bcs_domain::edge_permission::{EdgeGrant, GrantKind};
use bcs_domain::{
    BotAccessRelation, ManagementSource, TerminalReason, TransferAction, TransferStatus,
};

#[test]
fn direct_delete_does_not_revoke_team() {
    let team = ManagementSource::Team("team-a".into());
    assert!(!team.revocable_by_direct_api());
    assert!(ManagementSource::Direct.revocable_by_direct_api());
    assert!(ManagementSource::OwnershipTransfer("t-1".into()).revocable_by_direct_api());
    assert_eq!(team.storage_parts(), ("team", "team-a"));
}

#[test]
fn management_source_storage_encoding_is_stable() {
    assert_eq!(ManagementSource::Direct.storage_parts(), ("direct", "manual"));
    assert_eq!(
        ManagementSource::OwnershipTransfer("t-42".into()).storage_parts(),
        ("ownership_transfer", "t-42")
    );
    assert_eq!(
        ManagementSource::Team("team-b".into()).storage_parts(),
        ("team", "team-b")
    );
}

#[test]
fn owner_source_encoding_is_fixed_owner_owner() {
    // Owner never uses the manager ManagementSource enum; its edge
    // source is the fixed owner/owner pair.
    assert_eq!(
        BotAccessRelation::Owner.source_parts(&ManagementSource::Direct),
        ("owner", "owner")
    );
    assert_eq!(
        BotAccessRelation::Owner.source_parts(&ManagementSource::Team("t".into())),
        ("owner", "owner")
    );
    assert_eq!(
        BotAccessRelation::Manager.source_parts(&ManagementSource::Direct),
        ("direct", "manual")
    );
}

#[test]
fn management_source_serde_roundtrip() {
    let sources = [
        ManagementSource::Direct,
        ManagementSource::Team("team-a".into()),
        ManagementSource::OwnershipTransfer("t-1".into()),
    ];
    for source in sources {
        let text = serde_json::to_string(&source).unwrap();
        let back: ManagementSource = serde_json::from_str(&text).unwrap();
        assert_eq!(back, source);
    }
}

#[test]
fn management_source_unknown_value_rejected() {
    assert!(serde_json::from_str::<ManagementSource>("\"owner\"").is_err());
    assert!(serde_json::from_str::<ManagementSource>("\"legacy\"").is_err());
    assert!(serde_json::from_str::<ManagementSource>(r#"{"unknown":"x"}"#).is_err());
}

#[test]
fn empty_team_or_transfer_source_id_rejected() {
    assert!(ManagementSource::team("".to_string()).is_err());
    assert!(ManagementSource::ownership_transfer("".to_string()).is_err());
    // Whitespace-only is still empty.
    assert!(ManagementSource::team("   ".to_string()).is_err());
    assert!(ManagementSource::ownership_transfer(" ".to_string()).is_err());
    // JSON decode with an empty/whitespace id must fail too, not
    // default into a valid source.
    assert!(serde_json::from_str::<ManagementSource>(r#"{"team":""}"#).is_err());
    assert!(
        serde_json::from_str::<ManagementSource>(r#"{"ownership_transfer":"  "}"#)
            .is_err()
    );
}

#[test]
fn role_source_decode_is_strict() {
    use bcs_domain::{DecodedRoleSource, decode_role_source};

    assert_eq!(
        decode_role_source("owner", "owner").unwrap(),
        DecodedRoleSource::Owner
    );
    assert_eq!(
        decode_role_source("direct", "manual").unwrap(),
        DecodedRoleSource::Manager(ManagementSource::Direct)
    );
    assert_eq!(
        decode_role_source("team", "team-a").unwrap(),
        DecodedRoleSource::Manager(ManagementSource::Team("team-a".into()))
    );
    // Non-role encoding is NOT a valid role source.
    assert!(decode_role_source("none", "none").is_err());
    // Unknown kinds never default to direct.
    assert!(decode_role_source("legacy", "x").is_err());
    assert!(decode_role_source("", "").is_err());
    assert!(decode_role_source("direct", "self_chosen").is_err());
    assert!(decode_role_source("team", "").is_err());
    assert!(decode_role_source("ownership_transfer", "").is_err());
}

#[test]
fn non_role_source_encoding_is_fixed() {
    assert!(bcs_domain::is_non_role_source("none", "none"));
    assert!(!bcs_domain::is_non_role_source("none", "other"));
    assert!(!bcs_domain::is_non_role_source("direct", "manual"));
}

#[test]
fn access_relation_serde_roundtrip_and_unknown_rejected() {
    for relation in [BotAccessRelation::Owner, BotAccessRelation::Manager] {
        let text = serde_json::to_string(&relation).unwrap();
        assert_eq!(text, format!("\"{}\"", if relation == BotAccessRelation::Owner { "owner" } else { "manager" }));
        let back: BotAccessRelation = serde_json::from_str(&text).unwrap();
        assert_eq!(back, relation);
    }
    assert!(serde_json::from_str::<BotAccessRelation>("\"admin\"").is_err());
    assert!(serde_json::from_str::<BotAccessRelation>("\"creator\"").is_err());
}

#[test]
fn role_edge_shape_is_fixed() {
    // Owner edge: source encoding is fixed owner/owner regardless of the
    // manager-source argument (owner never uses the manager enum).
    let owner = EdgeGrant::new_role(
        1,
        "prod",
        "human_88001",
        "bot-a",
        BotAccessRelation::Owner,
        &ManagementSource::Direct,
    );
    assert_eq!(owner.grant_kind, GrantKind::Owner);
    assert_eq!(owner.management_source_parts(), ("owner", "owner"));
    assert_eq!(owner.grant_ref_id, bcs_domain::ROLE_GRANT_REF_ID);
    assert_eq!(bcs_domain::ROLE_GRANT_REF_ID, 0);
    assert_eq!(owner.rules, None);
    assert_eq!(owner.originator_policy_type, bcs_domain::edge_permission::OriginatorPolicyType::SameAsFrom);

    // Manager edge: source comes from the manager-only enum.
    let manager = EdgeGrant::new_role(
        2,
        "prod",
        "human_88001",
        "bot-a",
        BotAccessRelation::Manager,
        &ManagementSource::Team("team-a".into()),
    );
    assert_eq!(manager.grant_kind, GrantKind::Manager);
    assert_eq!(manager.management_source_parts(), ("team", "team-a"));
    assert_eq!(manager.grant_ref_id, 0);
    assert_eq!(manager.rules, None);
    assert_eq!(manager.originator_policy_type, bcs_domain::edge_permission::OriginatorPolicyType::SameAsFrom);
}

#[test]
fn non_role_edge_uses_fixed_none_none_source() {
    let friend_edge = EdgeGrant::new_non_role(
        3,
        "dev",
        "bot-a",
        "human_88001",
        GrantKind::PermissionProfile,
        42,
        None,
    );
    assert_eq!(friend_edge.management_source_parts(), ("none", "none"));
    assert!(friend_edge.is_non_role());
    assert_eq!(friend_edge.grant_kind, GrantKind::PermissionProfile);
    assert_eq!(friend_edge.grant_ref_id, 42);

    let rules_edge = EdgeGrant::new_non_role(
        4, "dev", "bot-a", "human_88001", GrantKind::Rules, 7,
        Some(serde_json::json!({"tool": "*"})),
    );
    assert_eq!(rules_edge.management_source_parts(), ("none", "none"));
}

#[test]
fn edge_grant_serde_roundtrip_keeps_source_fields() {
    let edge = EdgeGrant::new_role(
        5, "dev", "human_a", "bot-x",
        BotAccessRelation::Manager,
        &ManagementSource::OwnershipTransfer("t-9".into()),
    );
    let text = serde_json::to_string(&edge).unwrap();
    let back: EdgeGrant = serde_json::from_str(&text).unwrap();
    assert_eq!(back, edge);
    // No serde-default into "valid" roles: JSON without the source fields
    // must not decode.
    let missing_source = r#"{"edge_id":6,"env":"dev","from_id":"h","to_id":"b",
        "grant_kind":"manager","grant_ref_id":0,"status":"approved",
        "originator_policy_type":"same_as_from"}"#;
    assert!(serde_json::from_str::<EdgeGrant>(missing_source).is_err());
}

#[test]
fn runtime_grant_ref_shape_unchanged() {
    // Role edges are not A2A runtime grants; the slim runtime ref keeps
    // its original shape (kind + ref_id + source).
    let r = bcs_domain::edge_permission::AuthzGrantRef {
        kind: GrantKind::PermissionProfile,
        ref_id: 2,
        revision: None,
        digest: None,
        source: bcs_domain::edge_permission::GrantSource::EdgeGrant,
    };
    let text = serde_json::to_string(&r).unwrap();
    let back: bcs_domain::edge_permission::AuthzGrantRef = serde_json::from_str(&text).unwrap();
    assert_eq!(back.kind, GrantKind::PermissionProfile);
    assert_eq!(back.ref_id, 2);
    // New role kinds serialize to their fixed snake_case strings.
    assert_eq!(serde_json::to_string(&GrantKind::Owner).unwrap(), "\"owner\"");
    assert_eq!(serde_json::to_string(&GrantKind::Manager).unwrap(), "\"manager\"");
}

#[test]
fn transfer_state_serde_roundtrip_and_unknown_rejected() {
    let statuses = [
        TransferStatus::Pending,
        TransferStatus::Accepted,
        TransferStatus::Rejected,
        TransferStatus::Cancelled,
        TransferStatus::Expired,
        TransferStatus::Invalidated,
    ];
    for status in statuses {
        let text = serde_json::to_string(&status).unwrap();
        let back: TransferStatus = serde_json::from_str(&text).unwrap();
        assert_eq!(back, status);
    }
    assert!(serde_json::from_str::<TransferStatus>("\"done\"").is_err());

    for action in [TransferAction::Accept, TransferAction::Reject, TransferAction::Cancel] {
        let text = serde_json::to_string(&action).unwrap();
        let back: TransferAction = serde_json::from_str(&text).unwrap();
        assert_eq!(back, action);
    }
    assert!(serde_json::from_str::<TransferAction>("\"force\"").is_err());
}

#[test]
fn terminal_reasons_are_fixed_machine_reasons() {
    let reasons = [
        (TerminalReason::BotDeleted, "\"bot_deleted\""),
        (TerminalReason::ActorUnavailable, "\"actor_unavailable\""),
        (TerminalReason::OwnerChanged, "\"owner_changed\""),
    ];
    for (reason, text) in reasons {
        assert_eq!(serde_json::to_string(&reason).unwrap(), text);
        let back: TerminalReason = serde_json::from_str(text).unwrap();
        assert_eq!(back, reason);
    }
    assert!(serde_json::from_str::<TerminalReason>("\"bad_luck\"").is_err());
}
