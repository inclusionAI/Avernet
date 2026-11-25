//! AgentCard to generic Bot capability projection.
//!
//! The projection is shared by persistence and catalog publication so both
//! surfaces derive the same generic fields from the committed Card. Card
//! fields are used verbatim — nothing is derived from other Card sections.

use std::collections::HashSet;

use bcs_domain::Skill;
use serde_json::Value;

use crate::{ServiceError, ServiceResult};

/// AgentCard fields that replace the corresponding generic Bot fields.
///
/// `Some(Vec::new())` means the field is absent on the Card and the stored
/// generic value is cleared; a present field is used exactly as given.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ProjectedAgentCardCapabilities {
    pub summary: Option<String>,
    pub domains: Option<Vec<String>>,
    pub skills: Option<Vec<Skill>>,
    pub scopes: Option<Vec<String>>,
}

pub fn project_agent_card_capabilities(
    agent_card: Option<&Value>,
) -> ServiceResult<ProjectedAgentCardCapabilities> {
    let Some(card) = agent_card else {
        return Ok(Default::default());
    };
    if !card.is_object() {
        return Err(invalid("agent_card"));
    }

    let summary = match card.get("description") {
        Some(value) => Some(nonempty_string(value, "agent_card.description")?),
        None => None,
    };
    let skills = match card.get("skills") {
        Some(value) => Some(card_skills(value)?),
        None => None,
    };
    let domains = match card.get("domains") {
        Some(value) => Some(string_values(value, "agent_card.domains")?),
        None => None,
    };
    let scopes = match card.get("scopes") {
        Some(value) => Some(string_values(value, "agent_card.scopes")?),
        None => None,
    };

    Ok(ProjectedAgentCardCapabilities {
        summary,
        domains,
        scopes,
        skills,
    })
}

fn card_skills(value: &Value) -> ServiceResult<Vec<Skill>> {
    let items = value
        .as_array()
        .ok_or_else(|| invalid("agent_card.skills"))?;
    let mut skills = Vec::with_capacity(items.len());
    for skill in items {
        let name = skill
            .get("id")
            .and_then(Value::as_str)
            .filter(|id| !id.trim().is_empty())
            .ok_or_else(|| invalid("agent_card.skills[].id"))?;
        let description = match skill.get("description") {
            Some(value) => Some(nonempty_string(value, "agent_card.skills[].description")?),
            None => None,
        };
        let tags = match skill.get("tags") {
            Some(value) => string_values(value, "agent_card.skills[].tags")?,
            None => Vec::new(),
        };
        skills.push(Skill {
            name: name.to_owned(),
            description,
            tags,
        });
    }
    Ok(skills)
}

fn string_values(value: &Value, field: &'static str) -> ServiceResult<Vec<String>> {
    let items = value
        .as_array()
        .ok_or_else(|| invalid(field))?;
    let mut values = Vec::with_capacity(items.len());
    let mut seen = HashSet::new();
    for item in items {
        let value = nonempty_string(item, field)?;
        if seen.insert(value.clone()) {
            values.push(value);
        }
    }
    Ok(values)
}

fn nonempty_string(value: &Value, field: &'static str) -> ServiceResult<String> {
    value
        .as_str()
        .filter(|value| !value.trim().is_empty())
        .map(str::to_owned)
        .ok_or_else(|| invalid(field))
}

fn invalid(field: &'static str) -> ServiceError {
    ServiceError::InvalidOperation {
        message: format!("{field} is invalid"),
        request_id: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn missing_card_projects_no_fields() {
        assert_eq!(
            project_agent_card_capabilities(None).unwrap(),
            ProjectedAgentCardCapabilities::default()
        );
    }

    #[test]
    fn present_empty_lists_clear_generic_card_fields() {
        let card = json!({
            "description": "Updated",
            "domains": [],
            "skills": [],
            "scopes": []
        });
        assert_eq!(
            project_agent_card_capabilities(Some(&card)).unwrap(),
            ProjectedAgentCardCapabilities {
                summary: Some("Updated".into()),
                domains: Some(Vec::new()),
                skills: Some(Vec::new()),
                scopes: Some(Vec::new()),
            }
        );
    }

    #[test]
    fn card_fields_are_used_verbatim_without_cross_derivation() {
        // Skill tags and securityRequirements never feed domains or scopes:
        // absent source fields clear the generic fields instead.
        let card = json!({
            "description": "Updated",
            "skills": [{
                "id": "query",
                "description": "Queries",
                "tags": ["orders", "billing", "orders"],
                "securityRequirements": [{
                    "schemes": {"oauth": {"list": ["orders:read"]}}
                }]
            }],
            "domains": ["billing"],
            "scopes": ["orders:read"]
        });
        let projected = project_agent_card_capabilities(Some(&card)).unwrap();
        assert_eq!(projected.summary, Some("Updated".into()));
        assert_eq!(projected.domains, Some(vec!["billing".into()]));
        assert_eq!(projected.scopes, Some(vec!["orders:read".into()]));
        assert_eq!(
            projected.skills,
            Some(vec![Skill {
                name: "query".into(),
                description: Some("Queries".into()),
                tags: vec!["orders".into(), "billing".into()],
            }])
        );
    }

    #[test]
    fn absent_card_fields_project_none_and_derive_nothing() {
        let card = json!({
            "description": "Updated",
            "skills": [{
                "id": "query",
                "tags": ["skill-domain"],
                "securityRequirements": [{"schemes": {"oauth": {"list": ["skill:read"]}}}]
            }],
            "securityRequirements": [{"schemes": {"oauth": {"list": ["derived:read"]}}}]
        });
        let projected = project_agent_card_capabilities(Some(&card)).unwrap();
        assert_eq!(projected.domains, None);
        assert_eq!(projected.scopes, None);
    }

    #[test]
    fn duplicate_values_are_deduplicated_per_field() {
        let card = json!({"domains":["orders","orders","billing"]});
        let projected = project_agent_card_capabilities(Some(&card)).unwrap();
        assert_eq!(projected.domains, Some(vec!["orders".into(), "billing".into()]));
        assert_eq!(projected.scopes, None);
    }

    #[test]
    fn incompatible_projection_values_are_rejected() {
        for card in [json!({"domains":"orders"}), json!({"scopes":42}),
                json!({"skills":[{"id":""}]}), json!({"skills":[{"id":"q","tags":"orders"}]}),
                json!({"description":42})] {
            assert!(
                project_agent_card_capabilities(Some(&card)).is_err(),
                "unexpected successful projection: {card}"
            );
        }
    }

    /// Cards admitted by management must project without errors: projection
    /// derives the generic fields from the very same committed Card, so a
    /// divergence between the two validators broke registration of cards that
    /// management had just declared valid (the `{}` anonymous alternative).
    ///
    /// The matrix covers every securityRequirements shape management accepts:
    /// absent, empty, both anonymous alternatives, declared schemes with and
    /// without scopes, mixed anonymous/scoped entries, and skill-level entries.
    /// It grows with the acceptance rules: extend it whenever management adds
    /// another shape instead of re-deriving the fixture by hand.
    #[test]
    fn management_admitted_cards_project_without_errors() {
        let scoped_scheme = json!({"schemes": {"oauth": {"list": ["orders:read"]}}});
        // Management requires an anonymous alternative in every requirements
        // list (ANONYMOUS_PATH_REQUIRED), so each root-level fixture pairs the
        // shape under test with the `{}` anonymous alternative.
        for requirements in [
            json!([]),
            json!([{}]),
            json!([{"schemes": {}}]),
            json!([{}, {"schemes": {}}]),
            json!([scoped_scheme.clone(), {}]),
            json!([{}, scoped_scheme.clone()]),
            json!([{"schemes": {"oauth": {"list": ["orders:read", "billing:read"]}}}, {}]),
            json!([{"schemes": {"oauth": {}, "oauth2": {"list": ["orders:read"]}}}, {}]),
            json!([{"schemes": {
                "oauth": {"list": ["orders:read"]},
                "httpAuth": {"list": ["billing:read"]}
            }}, {}]),
        ] {
            let card = anonymous_admission_card(json!({
                "securityRequirements": requirements,
                "skills": [{
                    "id": "query",
                    "name": "Query",
                    "description": "Queries",
                    "tags": ["orders"],
                    "securityRequirements": [scoped_scheme.clone(), {}]
                }]
            }));
            assert!(
                bcs_domain::a2a_management::validate_management_card(&card).is_ok(),
                "fixture rejected by management, adjust the matrix: {card}"
            );
            assert!(
                project_agent_card_capabilities(Some(&card)).is_ok(),
                "management-admitted card failed projection: {card}"
            );
        }
        // Absent root-level securityRequirements is admitted as well.
        let absent = anonymous_admission_card(json!({
            "skills": [{"id": "query", "name": "Query", "description": "Queries", "tags": []}]
        }));
        assert!(bcs_domain::a2a_management::validate_management_card(&absent).is_ok());
        assert!(project_agent_card_capabilities(Some(&absent)).is_ok());
        // Verbatim projection (no cross-field derivation): scopes only come
        // from the Card's own `scopes` field — securityRequirements scopes are
        // never derived into it.
        let scoped = anonymous_admission_card(json!({
            "securityRequirements": [scoped_scheme, {}],
            "skills": []
        }));
        let projected = project_agent_card_capabilities(Some(&scoped)).unwrap();
        assert_eq!(projected.scopes, None);
    }

    /// Minimal AgentCard that passes management admission: every field the
    /// validator requires, nothing else. `extras` overrides or extends it.
    fn anonymous_admission_card(extras: Value) -> Value {
        let mut card = json!({
            "name": "Projection Matrix Bot",
            "description": "Card accepted by management admission",
            "version": "1.0",
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "supportedInterfaces": [{
                "protocolBinding": "HTTP+JSON",
                "protocolVersion": "1.0",
                "url": "http://example.test/bcs/a2a"
            }],
            "capabilities": {"streaming": true, "pushNotifications": false},
            "skills": [],
            "securitySchemes": {"oauth": {}, "oauth2": {}, "httpAuth": {}}
        });
        for (key, value) in extras.as_object().into_iter().flatten() {
            card.as_object_mut().unwrap().insert(key.clone(), value.clone());
        }
        card
    }
}
