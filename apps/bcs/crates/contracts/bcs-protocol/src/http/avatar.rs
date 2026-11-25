//! Presence-aware wire avatar field for bot profile writes (bot-avatar
//! plan Task 5).
//!
//! `AvatarField` distinguishes an omitted `avatar_url` (nothing to change)
//! from an explicit value (including the empty string, which clears). Only
//! presence deserialization accepting a plain string keeps the legacy
//! request DTOs' derived `Serialize` shapes byte-compatible when the field
//! is omitted.

use serde::{Deserialize, Serialize};

/// Wire field for `avatar_url` on bot profile-write requests.
///
/// `None` means the field was omitted (keep the stored avatar): request
/// DTOs combine `#[serde(default)]` with [`AvatarField::is_omitted`] so an
/// omitted key never serializes and never reaches a write. Presence only
/// accepts a plain string — explicit `null` and every non-string value
/// fail deserialization, and the empty string means "clear", settled at
/// the adapter/application boundary (the service-api `AvatarChange::Clear`
/// transition, never resolved here).
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
#[serde(transparent)]
pub struct AvatarField(pub Option<String>);

impl AvatarField {
    pub fn is_omitted(&self) -> bool {
        self.0.is_none()
    }
}

impl<'de> Deserialize<'de> for AvatarField {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        String::deserialize(d).map(|s| Self(Some(s)))
    }
}

#[cfg(test)]
mod tests {
    use serde::Deserialize;
    use serde_json::json;

    use super::AvatarField;
    use crate::http::{AdminOnboardRequest, RegisterProviderBotRequest};

    /// Minimal wrapper with the exact serde shape every request DTO uses.
    #[derive(Deserialize)]
    struct Input {
        #[serde(default)]
        avatar_url: AvatarField,
    }

    #[test]
    fn omitted_avatar_url_is_none() {
        assert_eq!(serde_json::from_str::<Input>("{}").unwrap().avatar_url.0, None);
    }

    #[test]
    fn empty_string_is_presence() {
        assert_eq!(
            serde_json::from_str::<Input>(r#"{"avatar_url":""}"#)
                .unwrap()
                .avatar_url
                .0,
            Some(String::new())
        );
    }

    #[test]
    fn plain_string_is_presence() {
        assert_eq!(
            serde_json::from_str::<Input>(r#"{"avatar_url":"https://cdn.example.com/a.png"}"#)
                .unwrap()
                .avatar_url
                .0,
            Some("https://cdn.example.com/a.png".to_string())
        );
    }

    #[test]
    fn explicit_null_is_rejected() {
        assert!(serde_json::from_str::<Input>(r#"{"avatar_url":null}"#).is_err());
    }

    #[test]
    fn non_string_values_are_rejected() {
        for body in [
            r#"{"avatar_url":7}"#,
            r#"{"avatar_url":true}"#,
            r#"{"avatar_url":["u"]}"#,
            r#"{"avatar_url":{"url":"u"}}"#,
        ] {
            assert!(
                serde_json::from_str::<Input>(body).is_err(),
                "expected rejection for {body}"
            );
        }
    }

    #[test]
    fn admin_onboard_request_accepts_and_echoes_an_avatar() {
        let request: AdminOnboardRequest = serde_json::from_value(json!({
            "bot_id": "bot-1",
            "name": "Bot",
            "avatar_url": "https://cdn.example.com/a.png"
        }))
        .unwrap();
        assert_eq!(
            request.avatar_url.0.as_deref(),
            Some("https://cdn.example.com/a.png")
        );
        assert_eq!(
            serde_json::to_value(request).unwrap()["avatar_url"],
            "https://cdn.example.com/a.png"
        );
    }

    #[test]
    fn admin_onboard_request_omits_avatar_when_absent() {
        let request: AdminOnboardRequest =
            serde_json::from_value(json!({"bot_id": "bot-1", "name": "Bot"})).unwrap();
        assert!(request.avatar_url.is_omitted());
        assert!(
            serde_json::to_value(request)
                .unwrap()
                .as_object()
                .unwrap()
                .get("avatar_url")
                .is_none()
        );
    }

    #[test]
    fn provider_bot_register_request_accepts_and_echoes_an_avatar() {
        let request: RegisterProviderBotRequest = serde_json::from_value(json!({
            "name": "Bot",
            "provider_bot_ref": "ref-1",
            "avatar_url": ""
        }))
        .unwrap();
        assert_eq!(request.avatar_url.0.as_deref(), Some(""));
        assert_eq!(serde_json::to_value(request).unwrap()["avatar_url"], "");
    }

    #[test]
    fn provider_bot_register_request_omits_avatar_when_absent() {
        let request: RegisterProviderBotRequest = serde_json::from_value(json!({
            "name": "Bot",
            "provider_bot_ref": "ref-1"
        }))
        .unwrap();
        assert!(request.avatar_url.is_omitted());
        assert!(
            serde_json::to_value(request)
                .unwrap()
                .as_object()
                .unwrap()
                .get("avatar_url")
                .is_none()
        );
    }
}
