//! Runtime view of the authoritative public Event Catalog.

use std::collections::HashSet;

use serde::Deserialize;
use thiserror::Error;

const SUPPORTED_SPEC_VERSION: &str = "1.0";
const SUPPORTED_SCHEMA_VERSION: &str = "1.0";

/// The checked-in external Contract is the only Event type inventory.
pub const EMBEDDED_EVENT_CATALOG_YAML: &str =
    include_str!("../../../../api-contracts/events/v1/catalog.yaml");

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EventCatalog {
    pub spec_version: String,
    pub families: Vec<CatalogFamily>,
    pub events: Vec<CatalogEvent>,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CatalogFamily {
    pub name: String,
    pub wildcard: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CatalogEvent {
    pub event_type: String,
    pub family: String,
    pub schema_version: String,
    pub subject_type: String,
    pub required_scope: Vec<String>,
    pub stream: EventStreamKind,
    #[serde(default)]
    pub content_fields: Vec<String>,
    pub data_schema: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventStreamKind {
    Group,
    Session,
    Task,
    StateMachineRun,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum CatalogError {
    #[error("event catalog is not valid YAML: {0}")]
    InvalidYaml(String),
    #[error("unsupported Event Catalog spec version: {0}")]
    UnsupportedSpecVersion(String),
    #[error("duplicate Event family name: {0}")]
    DuplicateFamilyName(String),
    #[error("duplicate Event family wildcard: {0}")]
    DuplicateFamilyWildcard(String),
    #[error("invalid Event family wildcard: {0}")]
    InvalidFamilyWildcard(String),
    #[error("duplicate Event type: {0}")]
    DuplicateEventType(String),
    #[error("invalid Event type: {0}")]
    InvalidEventType(String),
    #[error("Event {event_type} uses unknown family wildcard {family}")]
    UnknownFamily { event_type: String, family: String },
    #[error("Event {event_type} is outside family wildcard {family}")]
    EventOutsideFamily { event_type: String, family: String },
    #[error("Event {event_type} uses unsupported schema version {schema_version}")]
    UnsupportedSchemaVersion {
        event_type: String,
        schema_version: String,
    },
    #[error("Event {event_type} has an invalid data schema reference: {data_schema}")]
    InvalidDataSchemaReference {
        event_type: String,
        data_schema: String,
    },
}

impl EventCatalog {
    pub fn load_embedded() -> Result<Self, CatalogError> {
        Self::from_yaml(EMBEDDED_EVENT_CATALOG_YAML)
    }

    pub fn from_yaml(yaml: &str) -> Result<Self, CatalogError> {
        let catalog: Self = serde_yaml::from_str(yaml)
            .map_err(|error| CatalogError::InvalidYaml(error.to_string()))?;
        catalog.validate()?;
        Ok(catalog)
    }

    pub fn event(&self, event_type: &str) -> Option<&CatalogEvent> {
        self.events
            .iter()
            .find(|event| event.event_type == event_type)
    }

    pub fn is_registered_filter(&self, filter: &str) -> bool {
        self.event(filter).is_some() || self.families.iter().any(|family| family.wildcard == filter)
    }

    fn validate(&self) -> Result<(), CatalogError> {
        if self.spec_version != SUPPORTED_SPEC_VERSION {
            return Err(CatalogError::UnsupportedSpecVersion(
                self.spec_version.clone(),
            ));
        }

        let mut family_names = HashSet::new();
        let mut family_wildcards = HashSet::new();
        for family in &self.families {
            if !family_names.insert(family.name.as_str()) {
                return Err(CatalogError::DuplicateFamilyName(family.name.clone()));
            }
            if !family_wildcards.insert(family.wildcard.as_str()) {
                return Err(CatalogError::DuplicateFamilyWildcard(
                    family.wildcard.clone(),
                ));
            }
            if wildcard_prefix(&family.wildcard).is_none() {
                return Err(CatalogError::InvalidFamilyWildcard(family.wildcard.clone()));
            }
        }

        let mut event_types = HashSet::new();
        for event in &self.events {
            if !event_types.insert(event.event_type.as_str()) {
                return Err(CatalogError::DuplicateEventType(event.event_type.clone()));
            }
            if !is_legal_dotted_name(&event.event_type) {
                return Err(CatalogError::InvalidEventType(event.event_type.clone()));
            }
            if event.schema_version != SUPPORTED_SCHEMA_VERSION {
                return Err(CatalogError::UnsupportedSchemaVersion {
                    event_type: event.event_type.clone(),
                    schema_version: event.schema_version.clone(),
                });
            }
            if !family_wildcards.contains(event.family.as_str()) {
                return Err(CatalogError::UnknownFamily {
                    event_type: event.event_type.clone(),
                    family: event.family.clone(),
                });
            }
            let Some(prefix) = wildcard_prefix(&event.family) else {
                return Err(CatalogError::InvalidFamilyWildcard(event.family.clone()));
            };
            if !event.event_type.starts_with(prefix) {
                return Err(CatalogError::EventOutsideFamily {
                    event_type: event.event_type.clone(),
                    family: event.family.clone(),
                });
            }
            if !event.data_schema.starts_with("#/$defs/") {
                return Err(CatalogError::InvalidDataSchemaReference {
                    event_type: event.event_type.clone(),
                    data_schema: event.data_schema.clone(),
                });
            }
        }
        Ok(())
    }
}

fn wildcard_prefix(wildcard: &str) -> Option<&str> {
    let prefix = wildcard.strip_suffix('*')?;
    let namespace = prefix.strip_suffix('.')?;
    is_legal_namespace(namespace).then_some(prefix)
}

fn is_legal_dotted_name(value: &str) -> bool {
    value.split('.').count() >= 2 && is_legal_namespace(value)
}

fn is_legal_namespace(value: &str) -> bool {
    !value.is_empty() && value.split('.').all(is_legal_segment)
}

fn is_legal_segment(segment: &str) -> bool {
    let mut characters = segment.chars();
    let Some(first) = characters.next() else {
        return false;
    };
    first.is_ascii_lowercase()
        && characters.all(|character| {
            character.is_ascii_lowercase() || character.is_ascii_digit() || character == '_'
        })
}

#[cfg(test)]
mod tests {
    use super::{CatalogError, EventCatalog};

    #[test]
    fn embedded_catalog_is_the_runtime_event_inventory() {
        let Ok(catalog) = EventCatalog::load_embedded() else {
            panic!("embedded Event Catalog must be valid");
        };

        assert_eq!(catalog.events.len(), 16);
        assert!(catalog.event("group.created").is_some());
        assert!(catalog.event("message.created").is_some());
        assert!(catalog.event("judge.started").is_none());
    }

    #[test]
    fn filters_must_resolve_to_an_exact_event_or_registered_family() {
        let Ok(catalog) = EventCatalog::load_embedded() else {
            panic!("embedded Event Catalog must be valid");
        };

        assert!(catalog.is_registered_filter("message.created"));
        assert!(catalog.is_registered_filter("state_machine.*"));
        assert!(catalog.is_registered_filter("state_machine.node.*"));
        assert!(!catalog.is_registered_filter("state_machine.unknown.*"));
        assert!(!catalog.is_registered_filter("message.unknown"));
    }

    #[test]
    fn parser_rejects_events_that_reference_unregistered_families() {
        let yaml = r##"
spec_version: "1.0"
families:
  - name: message
    wildcard: message.*
events:
  - event_type: message.created
    family: unknown.*
    schema_version: "1.0"
    subject_type: message
    required_scope: [session_id]
    stream: session
    data_schema: "#/$defs/MessageCreatedData"
"##;

        assert_eq!(
            EventCatalog::from_yaml(yaml),
            Err(CatalogError::UnknownFamily {
                event_type: "message.created".to_owned(),
                family: "unknown.*".to_owned(),
            })
        );
    }
}
