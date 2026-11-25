//! Pure structural validation for an Agent-to-Agent invocation candidate.
//!
//! A valid shape is NOT authentication, Agent eligibility, production-domain
//! isolation, approval-generation freshness or an operation admission decision.
//! Those are mandatory before this kind can ever be consumed at runtime.

use crate::edge_permission::{EdgeGrant, GrantKind, OriginatorPolicyType};

#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
#[error("invalid Agent invocation edge shape")]
pub struct AgentInvokeShapeError;

/// Reject noncanonical AgentInvoke rows; do not redefine legacy kinds.
/// Bot IDs remain opaque and byte-exact; no trimming, code lookup or UUID
/// syntax assumption substitutes for the trusted identity resolution boundary.
pub fn validate_agent_invoke_shape(grant: &EdgeGrant) -> Result<(), AgentInvokeShapeError> {
    if grant.grant_kind != GrantKind::AgentInvoke {
        return Ok(());
    }
    if grant.env.is_empty() || grant.from_id.is_empty() || grant.to_id.is_empty()
        || grant.from_id == grant.to_id
        || grant.from_id.starts_with("human_") || grant.to_id.starts_with("human_")
        || grant.grant_ref_id != 0 || grant.rules.is_some()
        || grant.originator_policy_type != OriginatorPolicyType::SameAsFrom
        || grant.originator_policy_data.is_some()
    {
        return Err(AgentInvokeShapeError);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::edge_permission::{EdgeStatus, RequestKind};

    fn edge() -> EdgeGrant {
        EdgeGrant {
            edge_id: 0, env: "synthetic-production".into(),
            from_id: "bot-a".into(), to_id: "bot-b".into(),
            grant_kind: GrantKind::AgentInvoke, grant_ref_id: 0,
            rules: None, status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::SameAsFrom,
            originator_policy_data: None,
        }
    }

    #[test]
    fn all_kind_strings_are_stable_and_unknown_values_fail() {
        for (kind, text) in [
            (GrantKind::PermissionProfile, "permission_profile"),
            (GrantKind::Rules, "rules"), (GrantKind::Owner, "owner"),
            (GrantKind::Manager, "manager"), (GrantKind::AgentInvoke, "agent_invoke"),
        ] {
            let json = format!("\"{text}\"");
            assert_eq!(serde_json::to_string(&kind).unwrap(), json);
            assert_eq!(serde_json::from_str::<GrantKind>(&json).unwrap(), kind);
        }
        for (kind, text) in [
            (RequestKind::Connect, "connect"),
            (RequestKind::PermissionProfile, "permission_profile"),
            (RequestKind::Rules, "rules"), (RequestKind::Revoke, "revoke"),
            (RequestKind::AgentInvoke, "agent_invoke"),
        ] {
            let json = format!("\"{text}\"");
            assert_eq!(serde_json::to_string(&kind).unwrap(), json);
            assert_eq!(serde_json::from_str::<RequestKind>(&json).unwrap(), kind);
        }
        for text in ["system", "AgentInvoke", "SystemInvoke", "system_invoke", "unknown"] {
            assert!(serde_json::from_value::<GrantKind>(serde_json::json!(text)).is_err());
            assert!(serde_json::from_value::<RequestKind>(serde_json::json!(text)).is_err());
        }
    }

    #[test]
    fn canonical_shape_roundtrips_and_revocation_does_not_change_shape() {
        let mut grant = edge();
        assert!(validate_agent_invoke_shape(&grant).is_ok());
        assert_eq!(serde_json::from_str::<EdgeGrant>(
            &serde_json::to_string(&grant).unwrap()).unwrap(), grant);
        grant.status = EdgeStatus::Revoked;
        assert!(validate_agent_invoke_shape(&grant).is_ok());
        assert!(grant.grant_kind.is_runtime_candidate());
        assert!(!grant.grant_kind.is_legacy_runtime());
        assert!(!GrantKind::Owner.is_runtime_candidate());
        assert!(!GrantKind::Manager.is_runtime_candidate());
        assert!(GrantKind::Rules.is_legacy_runtime());
        assert!(GrantKind::PermissionProfile.is_legacy_runtime());
    }

    #[test]
    fn invalid_agent_shapes_are_rejected_without_changing_legacy_rules() {
        let mut cases = Vec::new();
        let mut g = edge(); g.grant_ref_id = 1; cases.push(g);
        let mut g = edge(); g.rules = Some(serde_json::Value::Null); cases.push(g);
        let mut g = edge(); g.rules = Some(serde_json::json!([])); cases.push(g);
        let mut g = edge(); g.originator_policy_type = OriginatorPolicyType::Any; cases.push(g);
        let mut g = edge(); g.originator_policy_data = Some(serde_json::json!([])); cases.push(g);
        let mut g = edge(); g.from_id = "human_1".into(); cases.push(g);
        let mut g = edge(); g.to_id = "human_2".into(); cases.push(g);
        let mut g = edge(); g.to_id = g.from_id.clone(); cases.push(g);
        let mut g = edge(); g.from_id.clear(); cases.push(g);
        let mut g = edge(); g.to_id.clear(); cases.push(g);
        let mut g = edge(); g.env.clear(); cases.push(g);
        for mut bad in cases {
            assert_eq!(validate_agent_invoke_shape(&bad), Err(AgentInvokeShapeError));
            bad.grant_kind = GrantKind::Rules;
            assert!(validate_agent_invoke_shape(&bad).is_ok());
        }
    }
}
