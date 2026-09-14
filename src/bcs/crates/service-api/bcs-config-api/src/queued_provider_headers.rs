//! Explicit opt-in for durable, non-credential Provider routing metadata.
use std::collections::BTreeMap;

pub const MAX_HEADERS: usize = 16;
pub const MAX_VALUE_BYTES: usize = 1024;
pub const MAX_TOTAL_BYTES: usize = 8192;

pub fn safe_name(name: &str) -> bool {
    let name = name.to_ascii_lowercase();
    !name.is_empty() && name.len() <= 128
        && name.bytes().all(|b| b.is_ascii_alphanumeric() || b"!#$%&'*+-.^_`|~".contains(&b))
        && !name.split(['-', '_']).any(|part| matches!(part,
            "authorization" | "cookie" | "token" | "secret" | "credential" | "credentials" | "password" | "key"))
        && !matches!(name.as_str(), "host" | "content-length" | "content-type" | "connection" | "transfer-encoding" | "bcn")
        && !name.starts_with("bcn-") && !name.starts_with("x-bcn-")
}

/// Configuration is an operator assertion that custom values are non-secret.
/// Never report input values in errors.
pub fn validate_config(bypass: &[String], persistable: &[String]) -> Result<(), String> {
    if persistable.len() > MAX_HEADERS { return Err("too many queue persistable headers".into()); }
    for name in persistable {
        if !safe_name(name) || !bypass.iter().any(|b| b.trim().eq_ignore_ascii_case(name)) {
            return Err("queue persistable headers must be non-sensitive names in bypass_headers".into());
        }
    }
    Ok(())
}

pub fn snapshot(headers: &[(String, String)], allowed: &[String]) -> Result<Vec<(String, String)>, &'static str> {
    if headers.len() > MAX_HEADERS { return Err("delivery_provider_headers_unsupported"); }
    let mut result = BTreeMap::new();
    let mut bytes = 0usize;
    for (name, value) in headers {
        if !safe_name(name) || !allowed.iter().any(|a| a.eq_ignore_ascii_case(name))
            || value.len() > MAX_VALUE_BYTES || value.bytes().any(|b| b < 32 || b == 127) {
            return Err("delivery_provider_headers_unsupported");
        }
        bytes = bytes.saturating_add(name.len()).saturating_add(value.len());
        if bytes > MAX_TOTAL_BYTES || result.insert(name.to_ascii_lowercase(), value.clone()).is_some() {
            return Err("delivery_provider_headers_unsupported");
        }
    }
    Ok(result.into_iter().collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn restricts_credentials_and_normalizes_bounded_snapshots() {
        let names = vec!["x-routing-zone".into()];
        validate_config(&names, &names).unwrap();
        assert_eq!(snapshot(&[("X-Routing-Zone".into(), "blue".into())], &names).unwrap(), vec![("x-routing-zone".into(), "blue".into())]);
        for name in ["cookie", "authorization", "x-api-key", "x-session-token", "connection"] {
            assert!(validate_config(&[name.into()], &[name.into()]).is_err());
            assert!(snapshot(&[(name.into(), "secret".into())], &[name.into()]).is_err());
        }
        assert!(validate_config(&[], &names).is_err());
        assert!(snapshot(&[(names[0].clone(), "x".repeat(MAX_VALUE_BYTES + 1))], &names).is_err());
        assert!(snapshot(&[(names[0].clone(), "a\r\nb".into())], &names).is_err());
        assert!(snapshot(&[(names[0].clone(), "a".into()), (names[0].clone(), "b".into())], &names).is_err());
    }
}
