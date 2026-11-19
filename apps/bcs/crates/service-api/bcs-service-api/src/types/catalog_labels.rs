//! Config-governed catalog metadata, independent of raw Agent/Group Cards.
use std::collections::{BTreeMap, BTreeSet};
use serde::{Deserialize, Deserializer, Serialize};
use crate::{ServiceError, ServiceResult};

pub use bcs_domain::catalog_labels::{CatalogLabelsInput,present_input};

/// Normalized metadata. Only nonempty, unique string lists; never raw JSON.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize)]
#[serde(transparent)]
pub struct CatalogLabels(BTreeMap<String, Vec<String>>);
impl CatalogLabels {
    pub fn as_map(&self) -> &BTreeMap<String, Vec<String>> { &self.0 }
    pub fn is_empty(&self) -> bool { self.0.is_empty() }
    /// SQL NULL is the legacy empty collection. Invalid non-null data is a storage error.
    pub fn from_storage(raw: Option<&str>) -> ServiceResult<Self> {
        raw.map(|raw| serde_json::from_str(raw).map_err(|error| ServiceError::InternalError(format!("stored catalog_labels: {error}"))))
            .unwrap_or_else(|| Ok(Self::default()))
    }
}
impl<'de> Deserialize<'de> for CatalogLabels {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        let values = BTreeMap::<String, Vec<String>>::deserialize(d)?;
        for (key, list) in &values {
            if !valid_key(key) || reserved(key) || list.is_empty()
                || list.iter().any(|v| v.trim().is_empty())
                || list.iter().collect::<BTreeSet<_>>().len() != list.len() {
                return Err(serde::de::Error::custom("catalog_labels is not normalized"));
            }
        }
        Ok(Self(values))
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CatalogLabelRule {
    pub allowed_values: Vec<String>,
    pub allow_multiple: bool,
}

/// Immutable after construction. Configuration is loaded by bootstrap, not by this policy.
#[derive(Clone, Debug, Default)]
pub struct CatalogLabelPolicy(BTreeMap<String, CatalogLabelRule>);
fn invalid(field: &str, reason: &'static str) -> ServiceError {
    crate::types::a2a_bot::a2a_management::ManagementError::new(
        crate::types::a2a_bot::a2a_management::ManagementCode::InvalidRequest, field, reason).into()
}
fn reserved(key: &str) -> bool { matches!(key, "visibility" | "user_visibility") }
fn valid_key(key: &str) -> bool {
    !key.is_empty() && key.bytes().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'-'))
}
impl CatalogLabelPolicy {
    pub fn new(rules: BTreeMap<String, CatalogLabelRule>) -> ServiceResult<Self> {
        for (key, rule) in &rules {
            if !valid_key(key) || reserved(key) || rule.allowed_values.is_empty()
                || rule.allowed_values.iter().any(|v| v.trim().is_empty())
                || rule.allowed_values.iter().collect::<BTreeSet<_>>().len() != rule.allowed_values.len() {
                return Err(invalid(&format!("catalog_labels.{key}"), "INVALID_DICTIONARY"));
            }
        }
        Ok(Self(rules))
    }
    pub fn normalize(&self, input: CatalogLabelsInput) -> ServiceResult<CatalogLabels> {
        let mut result = BTreeMap::new();
        for (key, values) in input.0 {
            let field = format!("catalog_labels.{key}");
            let rule = self.0.get(&key).ok_or_else(|| invalid(&field, "UNKNOWN_KEY"))?;
            let mut normalized = Vec::new();
            for (index, value) in values.unwrap_or_default().into_iter().enumerate() {
                if !rule.allowed_values.contains(&value) {
                    return Err(invalid(&format!("{field}[{index}]"), "UNKNOWN_VALUE"));
                }
                if !normalized.contains(&value) { normalized.push(value); }
            }
            if !rule.allow_multiple && normalized.len() > 1 { return Err(invalid(&field, "MULTIPLE_VALUES_NOT_ALLOWED")); }
            if !normalized.is_empty() { result.insert(key, normalized); }
        }
        Ok(CatalogLabels(result))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn generic_dictionary_normalizes_and_rejects_entire_invalid_input() {
        for multiple in [false, true] {
            let policy = CatalogLabelPolicy::new([("stage".into(), CatalogLabelRule {
                allowed_values: vec!["a".into(), "b".into()], allow_multiple: multiple,
            })].into()).unwrap();
            for input in [json!({}), json!({"stage":null}), json!({"stage":[]})] {
                assert!(policy.normalize(serde_json::from_value(input).unwrap()).unwrap().is_empty());
            }
            assert_eq!(policy.normalize(serde_json::from_value(json!({"stage":["b","b"]})).unwrap()).unwrap().as_map()["stage"], ["b"]);
            assert_eq!(policy.normalize(serde_json::from_value(json!({"stage":["a","b"]})).unwrap()).is_ok(), multiple);
            for input in [json!({"unknown":null}), json!({"unknown":[]}), json!({"stage":["A"]}), json!({"stage":[""]})] {
                assert!(policy.normalize(serde_json::from_value(input).unwrap()).is_err());
            }
        }
    }
    #[test]
    fn invalid_types_and_legacy_storage() {
        for value in [json!(null),json!(1),json!([]),json!("a"),json!({"a":1}),json!({"a":{}}),json!({"a":[null]}),json!({"a":[1]})] {
            assert!(serde_json::from_value::<CatalogLabelsInput>(value).is_err());
        }
        assert!(CatalogLabels::from_storage(None).unwrap().is_empty());
        for raw in ["null", "[]", "{\"a\":[]}", "{\"a\":null}", "{\"a\":[\"a\",\"a\"]}", "broken"] {
            assert!(CatalogLabels::from_storage(Some(raw)).is_err());
        }
        assert!(CatalogLabelPolicy::default().normalize(CatalogLabelsInput::default()).is_ok());
        assert!(CatalogLabelPolicy::default().normalize(serde_json::from_value(json!({"unknown":null})).unwrap()).is_err());
    }
    #[test]
    fn dictionary_is_fail_closed() {
        for key in ["visibility", "user_visibility", "", "bad.key"] {
            assert!(CatalogLabelPolicy::new([(key.into(), CatalogLabelRule {allowed_values:vec!["a".into()],allow_multiple:false})].into()).is_err());
        }
        for values in [vec![],vec![" ".into()],vec!["a".into(),"a".into()]] {
            assert!(CatalogLabelPolicy::new([("stage".into(), CatalogLabelRule {allowed_values:values,allow_multiple:true})].into()).is_err());
        }
    }
}
