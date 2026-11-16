//! Provider-scoped defaults for public AgentCard registration.
use serde::{Deserialize, Deserializer, Serialize};
use serde_json::Value;

/// Omitted default_owner preserves configuration; explicit null clears it.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProviderA2aPatch {
    #[serde(default, skip_serializing_if = "Option::is_none", deserialize_with = "present")]
    pub default_owner: Option<Option<String>>,
}
fn present<'de, D: Deserializer<'de>>(d: D) -> Result<Option<Option<String>>, D::Error> {
    Option::<String>::deserialize(d).map(Some)
}

pub fn valid_default_owner(id: &str) -> bool {
    !id.is_empty() && id.len() <= 128 && id == id.trim()
        && !id.starts_with("human_") && !id.chars().any(char::is_control)
}
impl ProviderA2aPatch {
    /// Merge only this setting; preserve unrelated Provider and A2A keys.
    pub fn apply(&self, config: &str) -> Result<String, String> {
        let Some(owner) = &self.default_owner else { return Ok(config.into()); };
        if owner.as_deref().is_some_and(|id| !valid_default_owner(id)) {
            return Err("a2a.default_owner must be a valid raw user id (1-128 bytes)".into());
        }
        let mut value: Value = serde_json::from_str(config).map_err(|_| "invalid Provider config")?;
        let object = value.as_object_mut().ok_or("invalid Provider config")?;
        if !object.contains_key("a2a") { object.insert("a2a".into(), serde_json::json!({})); }
        let a2a = object.get_mut("a2a").and_then(Value::as_object_mut).ok_or("invalid Provider a2a config")?;
        match owner {
            Some(id) => { a2a.insert("default_owner".into(), Value::String(id.clone())); }
            None => { a2a.remove("default_owner"); }
        }
        serde_json::to_string(&value).map_err(|_| "invalid Provider config".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn patch_preserves_sets_and_clears_only_owner() {
        let original = json!({"downlink":{"enabled":true},"a2a":{"future":42,"default_owner":"123"}}).to_string();
        let keep: ProviderA2aPatch = serde_json::from_value(json!({})).unwrap();
        assert_eq!(keep.apply(&original).unwrap(),original);
        for owner in [json!("456"), Value::Null] {
            let patch: ProviderA2aPatch = serde_json::from_value(json!({"default_owner":owner})).unwrap();
            let updated: Value = serde_json::from_str(&patch.apply(&original).unwrap()).unwrap();
            assert_eq!(updated["downlink"]["enabled"],true);
            assert_eq!(updated["a2a"]["future"],42);
            assert_eq!(updated["a2a"]["default_owner"],owner);
        }
        for id in ["", " ", " 123", "human_123", "12\n3", &"x".repeat(129)] {
            assert!(ProviderA2aPatch {default_owner:Some(Some(id.into()))}.apply(&original).is_err());
        }
        assert!(serde_json::from_value::<ProviderA2aPatch>(json!({"default_owner":42})).is_err());
        assert!(serde_json::from_value::<ProviderA2aPatch>(json!({"unknown":true})).is_err());
    }
}
