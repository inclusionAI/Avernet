use serde::Deserialize;
use crate::config::{ConfigError, ConnectionMode};

/// Opt-in HTTP delivery adapter. The URL is configuration, never a Host header.
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct A2aConfig {
    /// Externally reachable Bridge root, including any reverse-proxy prefix.
    pub public_url: String,
}

impl A2aConfig {
    pub(crate) fn validate(&self, mode: ConnectionMode) -> Result<(), ConfigError> {
        if mode != ConnectionMode::Gateway {
            return Err(ConfigError::Invalid("a2a requires gateway mode"));
        }
        let url = url::Url::parse(&self.public_url)
            .map_err(|_| ConfigError::Invalid("a2a.public_url must be an absolute HTTP(S) URL"))?;
        let loopback = match url.host() {
            Some(url::Host::Ipv4(ip)) => ip.is_loopback(),
            Some(url::Host::Ipv6(ip)) => ip.is_loopback(),
            Some(url::Host::Domain(host)) => host == "localhost",
            None => false,
        };
        if url.host().is_none() || !(url.scheme() == "https" || (url.scheme() == "http" && loopback))
            || !url.username().is_empty() || url.password().is_some()
            || url.query().is_some() || url.fragment().is_some() {
            return Err(ConfigError::Invalid("a2a.public_url requires HTTPS (HTTP on loopback only), without credentials, query or fragment"));
        }
        Ok(())
    }

    pub(crate) fn validate_id(id: &str) -> Result<(), ConfigError> {
        if id.trim().is_empty() || id.len() > 256 || matches!(id, "." | "..") || id.chars().any(char::is_control) {
            return Err(ConfigError::Invalid("A2A Provider/Bot references must be nonempty path segments, at most 256 bytes, without control characters or dot segments"));
        }
        Ok(())
    }

    pub(crate) fn endpoint(&self, provider: &str, bot: &str) -> String {
        // Configuration is validated before constructing the router.
        let mut url = url::Url::parse(&self.public_url).expect("validated A2A URL");
        url.path_segments_mut().expect("HTTP URL").pop_if_empty().extend(["a2a", provider, bot]);
        url.into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn public_url_validation_and_opaque_path_segments() {
        for value in ["https://agents.example/bridge", "http://127.0.0.1:21100", "http://[::1]:21100", "http://localhost:21100/"] {
            let config = A2aConfig { public_url: value.into() };
            config.validate(ConnectionMode::Gateway).unwrap();
            assert!(config.validate(ConnectionMode::Plugin).is_err());
        }
        for value in ["http://agents.example", "ftp://localhost/", "/relative", "https://user:secret@agents.example", "https://agents.example?key=secret", "https://agents.example/#fragment"] {
            let error = A2aConfig { public_url: value.into() }.validate(ConnectionMode::Gateway).unwrap_err();
            assert!(!error.to_string().contains("secret"));
        }
        let config = A2aConfig { public_url: "https://agents.example/prefix/".into() };
        assert_eq!(config.endpoint("team/a", "worker?#中"), "https://agents.example/prefix/a2a/team%2Fa/worker%3F%23%E4%B8%AD");
    }
    #[test]
    fn unknown_keys_and_wrong_types_fail_closed() {
        assert!(toml::from_str::<A2aConfig>("public_url='https://agents.example'\nstreamming=true").is_err());
        assert!(toml::from_str::<A2aConfig>("public_url=123").is_err());
        assert!(toml::from_str::<A2aConfig>("").is_err());
    }
}
