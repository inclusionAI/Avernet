//! Group-level opening-message configuration and deterministic rendering.

use std::collections::BTreeMap;

use serde::{Deserialize, Deserializer, Serialize};
use serde_json::Value;

pub const MAX_OPENING_MESSAGE_BYTES: usize = 64 * 1024;
pub const MAX_OPENING_MESSAGE_COMPONENT_BYTES: usize = 256;

const GROUP_ID_TOKEN: &str = "{{bcs.group_id}}";
const SESSION_ID_TOKEN: &str = "{{bcs.session_id}}";
const RUN_ID_TOKEN: &str = "{{bcs.run_id}}";
const GROUP_NAME_TOKEN: &str = "{{bcs.group_name}}";
const SESSION_NAME_TOKEN: &str = "{{bcs.session_name}}";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AixUiOpeningMessageType {
    Card,
    Panel,
}

impl AixUiOpeningMessageType {
    fn as_str(self) -> &'static str {
        match self {
            Self::Card => "card",
            Self::Panel => "panel",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AixUiOpeningTab {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub closable: Option<bool>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AixUiOpeningMessage {
    #[serde(rename = "type")]
    pub message_type: AixUiOpeningMessageType,
    pub component: String,
    #[serde(
        default,
        deserialize_with = "deserialize_present_object",
        skip_serializing_if = "Option::is_none"
    )]
    pub params: Option<BTreeMap<String, Value>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tab: Option<AixUiOpeningTab>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum OpeningMessage {
    Text(String),
    AixUi(AixUiOpeningMessage),
}

fn deserialize_present_object<'de, D>(
    deserializer: D,
) -> Result<Option<BTreeMap<String, Value>>, D::Error>
where
    D: Deserializer<'de>,
{
    BTreeMap::<String, Value>::deserialize(deserializer).map(Some)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OpeningMessageScope {
    Session,
    StateMachineRun,
}

#[derive(Debug, Clone, Copy)]
pub enum OpeningMessageRenderContext<'a> {
    Session {
        group_id: &'a str,
        session_id: &'a str,
        group_name: Option<&'a str>,
        session_name: Option<&'a str>,
    },
    StateMachineRun {
        group_id: &'a str,
        session_id: &'a str,
        run_id: &'a str,
        group_name: Option<&'a str>,
        session_name: Option<&'a str>,
    },
}

impl<'a> OpeningMessageRenderContext<'a> {
    fn scope(self) -> OpeningMessageScope {
        match self {
            Self::Session { .. } => OpeningMessageScope::Session,
            Self::StateMachineRun { .. } => OpeningMessageScope::StateMachineRun,
        }
    }

    fn value(self, token: &str) -> &'a str {
        match (token, self) {
            (GROUP_ID_TOKEN, Self::Session { group_id, .. })
            | (GROUP_ID_TOKEN, Self::StateMachineRun { group_id, .. }) => group_id,
            (SESSION_ID_TOKEN, Self::Session { session_id, .. })
            | (SESSION_ID_TOKEN, Self::StateMachineRun { session_id, .. }) => session_id,
            (RUN_ID_TOKEN, Self::StateMachineRun { run_id, .. }) => run_id,
            (GROUP_NAME_TOKEN, Self::Session { group_name, .. })
            | (GROUP_NAME_TOKEN, Self::StateMachineRun { group_name, .. }) => {
                group_name.unwrap_or_default()
            }
            (SESSION_NAME_TOKEN, Self::Session { session_name, .. })
            | (SESSION_NAME_TOKEN, Self::StateMachineRun { session_name, .. }) => {
                session_name.unwrap_or_default()
            }
            _ => unreachable!("validated template variable for render scope"),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RenderedOpeningMessage {
    pub content: String,
    pub component: Option<String>,
}

#[derive(Debug, Clone, thiserror::Error, PartialEq, Eq)]
pub enum OpeningMessageError {
    #[error("opening_message must not be empty")]
    Empty,
    #[error("opening_message exceeds the {MAX_OPENING_MESSAGE_BYTES}-byte limit")]
    TooLarge,
    #[error("opening_message contains unsupported template variable '{0}'")]
    UnsupportedTemplateVariable(String),
    #[error("opening_message contains an unterminated template variable")]
    UnterminatedTemplateVariable,
    #[error("opening_message.component must be 1-{MAX_OPENING_MESSAGE_COMPONENT_BYTES} bytes and contain no whitespace, control characters, quotes, '<', or '>'")]
    InvalidComponent,
    #[error("opening_message.tab is not supported when type is 'card'")]
    CardWithTab,
    #[error("opening_message could not be serialized: {0}")]
    Serialization(String),
}

impl OpeningMessage {
    pub fn validate(&self) -> Result<(), OpeningMessageError> {
        self.validate_for(OpeningMessageScope::StateMachineRun)
    }

    pub fn validate_for(&self, scope: OpeningMessageScope) -> Result<(), OpeningMessageError> {
        match self {
            Self::Text(template) => {
                if template.trim().is_empty() {
                    return Err(OpeningMessageError::Empty);
                }
                ensure_size(template)?;
                validate_template(template, scope)
            }
            Self::AixUi(message) => {
                validate_component(&message.component)?;
                if message.message_type == AixUiOpeningMessageType::Card && message.tab.is_some() {
                    return Err(OpeningMessageError::CardWithTab);
                }
                if let Some(params) = &message.params {
                    for value in params.values() {
                        validate_value_templates(value, scope)?;
                    }
                }
                if let Some(tab) = &message.tab {
                    for template in [tab.id.as_deref(), tab.title.as_deref()].into_iter().flatten() {
                        validate_template(template, scope)?;
                    }
                }
                let canonical = serde_json::to_string(message)
                    .map_err(|error| OpeningMessageError::Serialization(error.to_string()))?;
                ensure_size(&canonical)
            }
        }
    }

    pub fn render(
        &self,
        context: OpeningMessageRenderContext<'_>,
    ) -> Result<RenderedOpeningMessage, OpeningMessageError> {
        self.validate_for(context.scope())?;
        let rendered = match self {
            Self::Text(template) => RenderedOpeningMessage {
                content: render_template(template, context),
                component: None,
            },
            Self::AixUi(message) => {
                let tab = message
                    .tab
                    .as_ref()
                    .map(|tab| {
                        let value = serde_json::to_value(tab)
                            .map_err(|error| OpeningMessageError::Serialization(error.to_string()))?;
                        render_value(value, context)
                    })
                    .transpose()?;
                let params = message
                    .params
                    .as_ref()
                    .map(|params| render_value(Value::Object(to_sorted_map(params)), context))
                    .transpose()?;
                let mut lines = vec![
                    "<AixUI".to_string(),
                    format!("  type=\"{}\"", message.message_type.as_str()),
                    format!("  component=\"{}\"", message.component),
                ];
                if let Some(tab) = tab {
                    lines.push(format!("  tab='{}'", serialize_attribute(&tab)?));
                }
                if let Some(params) = params {
                    lines.push(format!("  params='{}'", serialize_attribute(&params)?));
                }
                lines.push("/>".to_string());
                RenderedOpeningMessage {
                    content: lines.join("\n"),
                    component: Some(message.component.clone()),
                }
            }
        };
        ensure_size(&rendered.content)?;
        Ok(rendered)
    }
}

fn validate_component(component: &str) -> Result<(), OpeningMessageError> {
    if component.is_empty()
        || component.len() > MAX_OPENING_MESSAGE_COMPONENT_BYTES
        || component.trim() != component
        || component.chars().any(|character| {
            character.is_whitespace()
                || character.is_control()
                || matches!(character, '\'' | '"' | '<' | '>')
        })
    {
        return Err(OpeningMessageError::InvalidComponent);
    }
    Ok(())
}

fn validate_value_templates(
    value: &Value,
    scope: OpeningMessageScope,
) -> Result<(), OpeningMessageError> {
    match value {
        Value::String(template) => validate_template(template, scope),
        Value::Array(values) => values
            .iter()
            .try_for_each(|value| validate_value_templates(value, scope)),
        Value::Object(values) => values
            .values()
            .try_for_each(|value| validate_value_templates(value, scope)),
        Value::Null | Value::Bool(_) | Value::Number(_) => Ok(()),
    }
}

fn validate_template(
    template: &str,
    scope: OpeningMessageScope,
) -> Result<(), OpeningMessageError> {
    let mut remainder = template;
    while let Some(start) = remainder.find("{{") {
        remainder = &remainder[start..];
        let Some(end) = remainder.find("}}") else {
            return Err(OpeningMessageError::UnterminatedTemplateVariable);
        };
        let token = &remainder[..end + 2];
        let supported = matches!(
            token,
            GROUP_ID_TOKEN | SESSION_ID_TOKEN | GROUP_NAME_TOKEN | SESSION_NAME_TOKEN
        ) || (scope == OpeningMessageScope::StateMachineRun && token == RUN_ID_TOKEN);
        if !supported {
            return Err(OpeningMessageError::UnsupportedTemplateVariable(
                token.to_string(),
            ));
        }
        remainder = &remainder[end + 2..];
    }
    Ok(())
}

fn render_template(template: &str, context: OpeningMessageRenderContext<'_>) -> String {
    let mut result = String::with_capacity(template.len());
    let mut remainder = template;
    while let Some(start) = remainder.find("{{") {
        result.push_str(&remainder[..start]);
        let token_start = &remainder[start..];
        let end = token_start
            .find("}}")
            .expect("validated template must have a closing delimiter");
        let token = &token_start[..end + 2];
        result.push_str(context.value(token));
        remainder = &token_start[end + 2..];
    }
    result.push_str(remainder);
    result
}

fn render_value(
    value: Value,
    context: OpeningMessageRenderContext<'_>,
) -> Result<Value, OpeningMessageError> {
    Ok(match value {
        Value::String(template) => Value::String(render_template(&template, context)),
        Value::Array(values) => Value::Array(
            values
                .into_iter()
                .map(|value| render_value(value, context))
                .collect::<Result<Vec<_>, _>>()?,
        ),
        Value::Object(values) => {
            let sorted = values.into_iter().collect::<BTreeMap<_, _>>();
            let mut rendered = serde_json::Map::new();
            for (key, value) in sorted {
                rendered.insert(key, render_value(value, context)?);
            }
            Value::Object(rendered)
        }
        other => other,
    })
}

fn to_sorted_map(values: &BTreeMap<String, Value>) -> serde_json::Map<String, Value> {
    values
        .iter()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}

fn serialize_attribute(value: &Value) -> Result<String, OpeningMessageError> {
    serde_json::to_string(value)
        .map(|json| json.replace('\'', "\\u0027"))
        .map_err(|error| OpeningMessageError::Serialization(error.to_string()))
}

fn ensure_size(value: &str) -> Result<(), OpeningMessageError> {
    if value.len() > MAX_OPENING_MESSAGE_BYTES {
        return Err(OpeningMessageError::TooLarge);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn context<'a>() -> OpeningMessageRenderContext<'a> {
        OpeningMessageRenderContext::StateMachineRun {
            group_id: "bcs_grp_1",
            session_id: "session_1",
            run_id: "run_1",
            group_name: Some("发布检查"),
            session_name: Some("第一轮"),
        }
    }

    #[test]
    fn raw_text_without_variables_is_unchanged() {
        let message = OpeningMessage::Text("  原样输出\n".to_string());
        let rendered = message.render(context()).expect("render");
        assert_eq!(rendered.content, "  原样输出\n");
        assert_eq!(rendered.component, None);
    }

    #[test]
    fn template_substitution_is_single_pass() {
        let context = OpeningMessageRenderContext::StateMachineRun {
            group_id: "bcs_grp_1",
            session_id: "session_1",
            run_id: "run_1",
            group_name: Some("{{bcs.run_id}}"),
            session_name: Some("第一轮"),
        };
        let message = OpeningMessage::Text("{{bcs.group_name}}/{{bcs.run_id}}".to_string());
        assert_eq!(
            message.render(context).expect("render").content,
            "{{bcs.run_id}}/run_1"
        );
    }

    #[test]
    fn renders_all_variables_and_defaults_missing_names_to_empty_strings() {
        let message = OpeningMessage::Text(
            "{{bcs.group_id}}|{{bcs.session_id}}|{{bcs.run_id}}|{{bcs.group_name}}|{{bcs.session_name}}"
                .to_string(),
        );
        let context = OpeningMessageRenderContext::StateMachineRun {
            group_id: "bcs_grp_1",
            session_id: "session_1",
            run_id: "run_1",
            group_name: None,
            session_name: None,
        };
        assert_eq!(
            message.render(context).expect("render").content,
            "bcs_grp_1|session_1|run_1||"
        );
    }

    #[test]
    fn structured_message_is_stable_and_escapes_single_quotes() {
        let message = OpeningMessage::AixUi(AixUiOpeningMessage {
            message_type: AixUiOpeningMessageType::Panel,
            component: "releasePanel.RunOverview".to_string(),
            params: Some(BTreeMap::from([
                ("runId".to_string(), Value::String(RUN_ID_TOKEN.to_string())),
                ("title".to_string(), Value::String("Bob's run".to_string())),
            ])),
            tab: Some(AixUiOpeningTab {
                id: Some("run-{{bcs.run_id}}".to_string()),
                title: Some("{{bcs.group_name}} / {{bcs.session_name}}".to_string()),
                closable: Some(true),
            }),
        });
        assert_eq!(
            message.render(context()).expect("render").content,
            concat!(
                "<AixUI\n",
                "  type=\"panel\"\n",
                "  component=\"releasePanel.RunOverview\"\n",
                "  tab='{\"closable\":true,\"id\":\"run-run_1\",\"title\":\"发布检查 / 第一轮\"}'\n",
                "  params='{\"runId\":\"run_1\",\"title\":\"Bob\\u0027s run\"}'\n",
                "/>"
            )
        );
    }

    #[test]
    fn rejects_unknown_variables_and_card_tabs() {
        assert!(matches!(
            OpeningMessage::Text("{{group_id}}".to_string()).validate(),
            Err(OpeningMessageError::UnsupportedTemplateVariable(_))
        ));
        let message = OpeningMessage::AixUi(AixUiOpeningMessage {
            message_type: AixUiOpeningMessageType::Card,
            component: "releaseCard.RunSummary".to_string(),
            params: None,
            tab: Some(AixUiOpeningTab {
                id: None,
                title: None,
                closable: None,
            }),
        });
        assert_eq!(message.validate(), Err(OpeningMessageError::CardWithTab));
    }

    #[test]
    fn card_renders_inline_without_tab_and_rejects_null_params_or_unknown_fields() {
        let message = OpeningMessage::AixUi(AixUiOpeningMessage {
            message_type: AixUiOpeningMessageType::Card,
            component: "releaseCard.RunSummary".to_string(),
            params: Some(BTreeMap::from([(
                "groupId".to_string(),
                Value::String(GROUP_ID_TOKEN.to_string()),
            )])),
            tab: None,
        });
        assert_eq!(
            message.render(context()).expect("render").content,
            concat!(
                "<AixUI\n",
                "  type=\"card\"\n",
                "  component=\"releaseCard.RunSummary\"\n",
                "  params='{\"groupId\":\"bcs_grp_1\"}'\n",
                "/>"
            )
        );
        assert!(
            serde_json::from_value::<OpeningMessage>(serde_json::json!({
                "type": "panel",
                "component": "releasePanel.RunOverview",
                "params": null
            }))
            .is_err()
        );
        assert!(
            serde_json::from_value::<OpeningMessage>(serde_json::json!({
                "type": "panel",
                "component": "releasePanel.RunOverview",
                "position": "right"
            }))
            .is_err()
        );
    }

    #[test]
    fn enforces_input_and_rendered_byte_limits() {
        assert_eq!(
            OpeningMessage::Text("x".repeat(MAX_OPENING_MESSAGE_BYTES + 1)).validate(),
            Err(OpeningMessageError::TooLarge)
        );
        let message = OpeningMessage::Text(GROUP_ID_TOKEN.to_string());
        let huge_group_id = "x".repeat(MAX_OPENING_MESSAGE_BYTES + 1);
        let context = OpeningMessageRenderContext::StateMachineRun {
            group_id: &huge_group_id,
            session_id: "session_1",
            run_id: "run_1",
            group_name: None,
            session_name: None,
        };
        assert_eq!(message.render(context), Err(OpeningMessageError::TooLarge));
    }

    #[test]
    fn session_scope_renders_session_variables_and_rejects_run_id() {
        let context = OpeningMessageRenderContext::Session {
            group_id: "bcs_grp_1",
            session_id: "session_1",
            group_name: Some("自由聊天"),
            session_name: Some("第一轮"),
        };
        let message = OpeningMessage::Text(
            "{{bcs.group_name}}/{{bcs.session_name}}/{{bcs.group_id}}/{{bcs.session_id}}"
                .to_string(),
        );
        assert_eq!(
            message.render(context).expect("render").content,
            "自由聊天/第一轮/bcs_grp_1/session_1"
        );
        assert!(matches!(
            OpeningMessage::Text(RUN_ID_TOKEN.to_string())
                .validate_for(OpeningMessageScope::Session),
            Err(OpeningMessageError::UnsupportedTemplateVariable(token)) if token == RUN_ID_TOKEN
        ));
    }
}
