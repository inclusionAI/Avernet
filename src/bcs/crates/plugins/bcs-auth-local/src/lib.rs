//! Rule 20 local plugin + a static test double used by route contract tests.

use async_trait::async_trait;
use axum::http::HeaderMap;

use bcs_auth_api::{AuthError, AuthPlugin, AuthPrincipal, AuthSource, LocalAuthConfig};

/// Local-dev plugin: emits a mock principal from config (no IO).
pub struct LocalAuthPlugin {
    user_id: Option<String>,
    user_name: Option<String>,
    allow_mock_headers: bool,
}

impl LocalAuthPlugin {
    pub fn from_config(config: &LocalAuthConfig) -> Self {
        Self {
            user_id: config.mock_user_id.clone(),
            user_name: config.mock_user_name.clone(),
            allow_mock_headers: config.allow_mock_headers,
        }
    }

    fn mock_user_id_from_headers(&self, headers: &HeaderMap) -> Option<String> {
        if !self.allow_mock_headers {
            return None;
        }
        header_value(headers, "X-Mock-User-Id")
    }

    fn mock_user_name_from_headers(&self, headers: &HeaderMap) -> Option<String> {
        if !self.allow_mock_headers {
            return None;
        }
        header_value(headers, "X-Mock-Nick-Name")
    }
}

fn header_value(headers: &HeaderMap, name: &str) -> Option<String> {
    headers
        .get(name)
        .and_then(|value| value.to_str().ok())
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

#[async_trait]
impl AuthPlugin for LocalAuthPlugin {
    fn can_authenticate(&self, headers: &HeaderMap) -> bool {
        self.mock_user_id_from_headers(headers).is_some() || self.user_id.is_some()
    }
    async fn authenticate(
        &self,
        headers: &HeaderMap,
    ) -> Result<Option<AuthPrincipal>, AuthError> {
        match self.mock_user_id_from_headers(headers).or_else(|| self.user_id.clone()) {
            Some(user_id) if !user_id.is_empty() => {
                let mut p = AuthPrincipal::new(AuthSource::Local);
                p.user_id = Some(user_id);
                p.user_name = self
                    .mock_user_name_from_headers(headers)
                    .or_else(|| self.user_name.clone());
                Ok(Some(p))
            }
            _ => Ok(None),
        }
    }
    fn priority(&self) -> u8 {
        5
    }
    fn name(&self) -> &'static str {
        "local"
    }
}
/// Test double: always returns a preset principal. Used by route contract tests
/// to inject a fixed identity without any SDK.
pub struct StaticAuthPlugin {
    principal: AuthPrincipal,
}

impl StaticAuthPlugin {
    pub fn with_user_id(user_id: impl Into<String>) -> Self {
        let mut p = AuthPrincipal::new(AuthSource::Local);
        p.user_id = Some(user_id.into());
        Self { principal: p }
    }

    pub fn with_principal(principal: AuthPrincipal) -> Self {
        Self { principal }
    }
}

#[async_trait]
impl AuthPlugin for StaticAuthPlugin {
    fn can_authenticate(&self, _headers: &HeaderMap) -> bool {
        true
    }
    async fn authenticate(
        &self,
        _headers: &HeaderMap,
    ) -> Result<Option<AuthPrincipal>, AuthError> {
        Ok(Some(self.principal.clone()))
    }
    fn priority(&self) -> u8 {
        0
    }
    fn name(&self) -> &'static str {
        "static"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn local_emits_mock_principal() {
        let cfg = LocalAuthConfig {
            mock_user_id: Some("12345".into()),
            mock_user_name: Some("测试".into()),
            allow_mock_headers: false,
        };
        let p = LocalAuthPlugin::from_config(&cfg);
        assert!(p.can_authenticate(&HeaderMap::new()));
        let principal = p.authenticate(&HeaderMap::new()).await.unwrap().unwrap();
        assert_eq!(principal.user_id.as_deref(), Some("12345"));
        assert_eq!(principal.user_name.as_deref(), Some("测试"));
    }

    #[tokio::test]
    async fn local_none_without_user_id() {
        let p = LocalAuthPlugin::from_config(&LocalAuthConfig::default());
        assert!(!p.can_authenticate(&HeaderMap::new()));
        assert!(p.authenticate(&HeaderMap::new()).await.unwrap().is_none());
    }

    #[tokio::test]
    async fn local_ignores_x_mock_user_id_header_when_disabled() {
        let mut headers = HeaderMap::new();
        headers.insert("X-Mock-User-Id", "from-header".parse().unwrap());
        headers.insert("X-Mock-Nick-Name", "Header User".parse().unwrap());

        let without_config = LocalAuthPlugin::from_config(&LocalAuthConfig::default());
        assert!(!without_config.can_authenticate(&headers));
        assert!(without_config.authenticate(&headers).await.unwrap().is_none());

        let with_config = LocalAuthPlugin::from_config(&LocalAuthConfig {
            mock_user_id: Some("from-config".into()),
            mock_user_name: Some("Config User".into()),
            allow_mock_headers: false,
        });
        let principal = with_config.authenticate(&headers).await.unwrap().unwrap();
        assert_eq!(principal.user_id.as_deref(), Some("from-config"));
        assert_eq!(principal.user_name.as_deref(), Some("Config User"));
    }

    #[tokio::test]
    async fn local_uses_x_mock_headers_when_enabled() {
        let mut headers = HeaderMap::new();
        headers.insert("X-Mock-User-Id", "from-header".parse().unwrap());
        headers.insert("X-Mock-Nick-Name", "Header User".parse().unwrap());

        let plugin = LocalAuthPlugin::from_config(&LocalAuthConfig {
            allow_mock_headers: true,
            ..LocalAuthConfig::default()
        });

        assert!(plugin.can_authenticate(&headers));
        let principal = plugin.authenticate(&headers).await.unwrap().unwrap();
        assert_eq!(principal.user_id.as_deref(), Some("from-header"));
        assert_eq!(principal.user_name.as_deref(), Some("Header User"));
    }

    #[tokio::test]
    async fn local_header_identity_overrides_config_default_when_enabled() {
        let mut headers = HeaderMap::new();
        headers.insert("X-Mock-User-Id", "bob".parse().unwrap());

        let plugin = LocalAuthPlugin::from_config(&LocalAuthConfig {
            mock_user_id: Some("alice".into()),
            mock_user_name: Some("Alice".into()),
            allow_mock_headers: true,
        });

        let principal = plugin.authenticate(&headers).await.unwrap().unwrap();
        assert_eq!(principal.user_id.as_deref(), Some("bob"));
        assert_eq!(principal.user_name.as_deref(), Some("Alice"));
    }

    #[tokio::test]
    async fn static_returns_preset() {
        let p = StaticAuthPlugin::with_user_id("staff-1");
        let principal = p.authenticate(&HeaderMap::new()).await.unwrap().unwrap();
        assert_eq!(principal.user_id.as_deref(), Some("staff-1"));
    }
}
