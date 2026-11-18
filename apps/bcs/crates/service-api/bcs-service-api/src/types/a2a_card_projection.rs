//! AgentCard to generic Bot capability projection.
//!
//! The projection is shared by persistence and catalog publication so both
//! surfaces derive the same generic fields from the committed Card.

use std::collections::HashSet;

use bcs_domain::Skill;
use serde_json::Value;

use crate::{ServiceError, ServiceResult};

/// AgentCard fields that can replace the corresponding generic Bot fields.
///
/// `None` means the Card does not carry the source field and the stored Bot
/// value must be used. `Some(Vec::new())` means the field exists and therefore
/// clears the generic Bot field.
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
        None => skills.as_ref().map(|(_, tags)| tag_domains(tags)),
    };
    let scopes = if let Some(value) = card.get("scopes") {
        Some(string_values(value, "agent_card.scopes")?)
    } else {
        security_scopes(card)?
    };

    Ok(ProjectedAgentCardCapabilities {
        summary,
        domains,
        scopes,
        skills: skills.map(|(skills, _)| skills),
    })
}

fn card_skills(value: &Value) -> ServiceResult<(Vec<Skill>, Vec<Vec<String>>)> {
    let items = value
        .as_array()
        .ok_or_else(|| invalid("agent_card.skills"))?;
    let mut skills = Vec::with_capacity(items.len());
    let mut tags = Vec::with_capacity(items.len());
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
        let skill_tags = match skill.get("tags") {
            Some(value) => string_values(value, "agent_card.skills[].tags")?,
            None => Vec::new(),
        };
        skills.push(Skill {
            name: name.to_owned(),
            description,
        });
        tags.push(skill_tags);
    }
    Ok((skills, tags))
}

fn tag_domains(tags: &[Vec<String>]) -> Vec<String> {
    let mut domains = Vec::new();
    let mut seen = HashSet::new();
    for values in tags {
        for value in values {
            if seen.insert(value.clone()) {
                domains.push(value.clone());
            }
        }
    }
    domains
}

fn security_scopes(card: &Value) -> ServiceResult<Option<Vec<String>>> {
    let root_requirements = card.get("securityRequirements");
    let mut skill_requirements = Vec::new();
    if let Some(values) = card.get("skills").and_then(Value::as_array) {
        for skill in values {
            if let Some(value) = skill.get("securityRequirements") {
                skill_requirements
                    .push(("agent_card.skills[].securityRequirements", value));
            }
        }
    }
    if root_requirements.is_none() && skill_requirements.is_empty() {
        return Ok(None);
    }

    let mut scopes = Vec::new();
    let mut seen = HashSet::new();
    for (field, requirements) in root_requirements
        .into_iter()
        .map(|value| ("agent_card.securityRequirements", value))
        .chain(skill_requirements)
    {
        let requirements = requirements
            .as_array()
            .ok_or_else(|| invalid(field))?;
        for requirement in requirements {
            let requirement = requirement
                .as_object()
                .ok_or_else(|| invalid("agent_card.securityRequirements[].schemes"))?;
            // An empty alternative (`{}`) is the protocol's anonymous channel
            // (see `validate_anonymous_security`) and contributes no scopes.
            let Some(schemes) = requirement.get("schemes") else {
                if !requirement.is_empty() {
                    return Err(invalid("agent_card.securityRequirements[].schemes"));
                }
                continue;
            };
            let schemes = schemes
                .as_object()
                .ok_or_else(|| invalid("agent_card.securityRequirements[].schemes"))?;
            for scheme in schemes.values() {
                if let Some(values) = scheme.get("list") {
                    push_string_values(
                        values,
                        "agent_card.securityRequirements[].schemes.list",
                        &mut scopes,
                        &mut seen,
                    )?;
                }
            }
        }
    }
    Ok(Some(scopes))
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

fn push_string_values(
    value: &Value,
    field: &'static str,
    target: &mut Vec<String>,
    seen: &mut HashSet<String>,
) -> ServiceResult<()> {
    let items = value
        .as_array()
        .ok_or_else(|| invalid(field))?;
    for item in items {
        let value = nonempty_string(item, field)?;
        if seen.insert(value.clone()) {
            target.push(value);
        }
    }
    Ok(())
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
    fn missing_optional_sources_project_none_or_derive_card_data() {
        let card = json!({
            "description": "Updated",
            "skills": [{
                "id": "query",
                "description": "Queries",
                "tags": ["orders", "billing", "orders"],
                "securityRequirements": [{
                    "schemes": {"oauth": {"list": ["orders:read"]}}
                }]
            }]
        });
        let projected = project_agent_card_capabilities(Some(&card)).unwrap();
        assert_eq!(projected.domains, Some(vec!["orders".into(), "billing".into()]));
        assert_eq!(
            projected.skills,
            Some(vec![Skill {
                name: "query".into(),
                description: Some("Queries".into()),
            }])
        );
        assert_eq!(projected.scopes, Some(vec!["orders:read".into()]));
    }

    #[test]
    fn security_requirements_clear_scopes_when_top_level_scope_is_absent() {
        let card = json!({"description":"Updated","skills":[],"securityRequirements":[]});
        let projected = project_agent_card_capabilities(Some(&card)).unwrap();
        assert_eq!(projected.scopes, Some(Vec::new()));
    }

    #[test]
    fn top_level_real_field_takes_precedence_over_derived_fields() {
        let card = json!({
            "description":"Updated",
            "domains":["top"],
            "scopes":["top:read"],
            "securityRequirements":[{"schemes":{"oauth":{"list":["derived:read"]}}}],
            "skills":[{
                "id":"query",
                "tags":["skill-domain"],
                "securityRequirements":[{"schemes":{"oauth":{"list":["skill:read"]}}}]
            }]
        });
        let projected = project_agent_card_capabilities(Some(&card)).unwrap();
        assert_eq!(projected.domains, Some(vec!["top".into()]));
        assert_eq!(projected.scopes, Some(vec!["top:read".into()]));
    }

    #[test]
    fn anonymous_empty_security_alternative_projects_no_scopes() {
        // `{}` and `{"schemes":{}}` are the protocol's anonymous alternatives and
        // must both project the same as an absent anonymous entry: no scopes.
        for requirements in [json!([{}]), json!([{"schemes": {}}])] {
            let card = json!({
                "description": "Updated",
                "skills": [{
                    "id": "query",
                    "securityRequirements": [{}]
                }],
                "securityRequirements": requirements
            });
            let projected = project_agent_card_capabilities(Some(&card)).unwrap();
            assert_eq!(
                projected.scopes,
                Some(Vec::new()),
                "unexpected scopes for {requirements}"
            );
        }
        // An anonymous alternative mixes with a scoped one: only the scoped
        // entry contributes scopes, the anonymous one is skipped.
        let card = json!({
            "description": "Updated",
            "skills": [{
                "id": "query",
                "securityRequirements": [{}]
            }],
            "securityRequirements": [{}, {"schemes": {"oauth": {"list": ["orders:read"]}}}]
        });
        let projected = project_agent_card_capabilities(Some(&card)).unwrap();
        assert_eq!(projected.scopes, Some(vec!["orders:read".into()]));
    }

    #[test]
    fn non_empty_requirement_without_schemes_is_rejected() {
        let card = json!({"securityRequirements": [{"unknown": 1}]});
        assert!(project_agent_card_capabilities(Some(&card)).is_err());
    }

    #[test]
    fn incompatible_projection_values_are_rejected() {
        for card in [json!({"domains":"orders"}), json!({"scopes":42}),
                json!({"skills":[{"id":""}]}), json!({"description":42})] {
            assert!(
                project_agent_card_capabilities(Some(&card)).is_err(),
                "unexpected successful projection: {card}"
            );
        }
    }
}
