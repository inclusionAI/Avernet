//! Provider identity: how `register` obtains the Bot's `provider_bot_ref`.
//!
//! The resolver is selected with `register --provider-auth <id>` and must
//! match how the Provider authenticates its Bots. The default `static-bearer`
//! resolver uses `--provider-bot-ref`; distributions add resolvers for other
//! Provider types (for example one that reads a trusted agent identity).

use anyhow::{Result, bail};

use crate::registration::{Mode, RegistrationClient};
pub use bcs_bridge_core::config::BotConfig;

/// Registration inputs available to an identity resolver.
pub struct IdentityContext<'a> {
    pub mode: Mode,
    /// Client for the resolved registration API prefix.
    pub client: &'a RegistrationClient,
    /// Explicit or locally discovered Provider reference, validated before authentication.
    pub explicit_ref: Option<&'a str>,
}

#[async_trait::async_trait]
pub trait IdentityResolver: Send + Sync {
    /// Value accepted by `--provider-auth`.
    fn id(&self) -> &str;
    /// Environment checks run before any configuration or network access.
    fn preflight(&self) -> Result<()> { Ok(()) }
    /// Resolve a local identity from the selected engine and canonical workspace.
    /// Distributions may discover an existing identity; the default preserves CLI input.
    async fn registration_ref(&self, explicit_ref: Option<&str>, _engine: &str, _cwd: &std::path::Path) -> Result<Option<String>> {
        Ok(explicit_ref.map(str::to_owned))
    }
    /// Prepare authentication for both identity lookup and registration.
    /// Credentials remain in this short-lived client, outside saved configuration.
    async fn registration_client(&self, client: RegistrationClient, _explicit_ref: Option<&str>) -> Result<RegistrationClient> {
        Ok(client)
    }
    /// Persist only the selected authentication policy, never its credentials.
    fn runtime_auth(&self) -> &str { self.id() }
    /// Validate a runtime workspace before connecting the registered Bot.
    async fn validate_workspace(&self, _bot: &BotConfig, _api_url: Option<&str>) -> Result<()> { Ok(()) }
    /// The Bot's stable identity within its Provider. Called after local
    /// validation, immediately before the registration request.
    async fn provider_bot_ref(&self, ctx: &IdentityContext<'_>) -> Result<String>;
}

/// Static-bearer Providers: gateway Bots need an explicit `--provider-bot-ref`
/// (BCS addresses webhook delivery with it); plugin Bots get a generated
/// `bridge-<uuid>` reference when none is given.
pub struct StaticBearer;

#[async_trait::async_trait]
impl IdentityResolver for StaticBearer {
    fn id(&self) -> &str { "static-bearer" }

    async fn provider_bot_ref(&self, ctx: &IdentityContext<'_>) -> Result<String> {
        match (ctx.explicit_ref, ctx.mode) {
            (Some(reference), _) => Ok(reference.to_owned()),
            (None, Mode::Plugin) => Ok(format!("bridge-{}", uuid::Uuid::new_v4())),
            (None, Mode::Gateway) => bail!("--provider-bot-ref is required for gateway registration with a static-bearer Provider"),
        }
    }
}
