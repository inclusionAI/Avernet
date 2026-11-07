//! Configuration-driven public JWT plugin registration.
use std::sync::Arc;
use bcs_caller_identity_api::{CallerIdentityError, CallerIdentityPlugin, CallerIdentityPluginFactory};
use bcs_config_api::CallerIdentityServiceConfig;
use crate::{JwtCallerIdentityConfig, JwtCallerIdentityPlugin, TrustedPublicKey};

fn build(config: &CallerIdentityServiceConfig) -> Result<Arc<dyn CallerIdentityPlugin>, CallerIdentityError> {
    if !config.options.is_empty() {
        return Err(CallerIdentityError::InvalidRequest);
    }
    let jwt = config.jwt.as_ref().ok_or(CallerIdentityError::InvalidRequest)?;
    if jwt.issuer.trim().is_empty()
        || jwt.signing_kid.trim().is_empty()
        || jwt.pseudonym_key_hex.trim().is_empty()
    {
        return Err(CallerIdentityError::InvalidRequest);
    }
    let pseudonym_key = hex::decode(jwt.pseudonym_key_hex.trim())
        .map_err(|_| CallerIdentityError::InvalidRequest)?;
    let plugin = JwtCallerIdentityPlugin::new(JwtCallerIdentityConfig {
        issuer: jwt.issuer.clone(),
        signing_kid: jwt.signing_kid.clone(),
        signing_key_pem: jwt.signing_key_pem.clone(),
        public_keys: jwt.public_keys.iter().map(|key| TrustedPublicKey {
            kid: key.kid.clone(), public_pem: key.public_pem.clone(),
        }).collect(),
        pseudonym_key,
        max_ttl_seconds: jwt.max_ttl_seconds,
    }).map_err(|_| CallerIdentityError::InvalidRequest)?;
    Ok(Arc::new(plugin))
}

inventory::submit! {
    CallerIdentityPluginFactory { name: "jwt", build }
}
