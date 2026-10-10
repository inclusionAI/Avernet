//! Application assembly: the plugins and defaults a `bcs-bridge` binary runs with.
//!
//! [`BridgeApp::builder`] starts from the open-source defaults; a distribution
//! adds engines and identity resolvers, or replaces the deploy profile,
//! credential source and CLI defaults, then calls [`BridgeApp::main`].

use std::{collections::BTreeMap, process::ExitCode, sync::Arc};

use anyhow::Context;
use bcs_bridge_core::engine::{EngineFactory, EngineRegistry};

use crate::credentials::{CredentialSource, EnvCredentials};
use crate::identity::{IdentityResolver, StaticBearer};
use crate::profile::{DeployProfile, ExplicitProfile};

/// Help text and argument defaults of the binary.
#[derive(Clone, Copy)]
pub struct CliDefaults {
    pub about: &'static str,
    /// Default `register --engine`.
    pub engine: &'static str,
    /// Default `register --model`; none leaves the model to the engine.
    pub model: Option<&'static str>,
    /// Default `register --provider-auth`.
    pub provider_auth: &'static str,
}

impl Default for CliDefaults {
    fn default() -> Self {
        Self {
            about: "Register a BCS Bot and bridge it to a local coding-agent engine",
            engine: "claude-code",
            model: None,
            provider_auth: "static-bearer",
        }
    }
}

pub struct BridgeApp {
    engines: EngineRegistry,
    profile: Arc<dyn DeployProfile>,
    credentials: Arc<dyn CredentialSource>,
    identities: BTreeMap<String, Arc<dyn IdentityResolver>>,
    defaults: CliDefaults,
}

impl BridgeApp {
    /// Builder preloaded with the open-source defaults: the built-in engines,
    /// [`ExplicitProfile`], [`EnvCredentials`] and the [`StaticBearer`] identity.
    pub fn builder() -> BridgeAppBuilder {
        let mut identities: BTreeMap<String, Arc<dyn IdentityResolver>> = BTreeMap::new();
        identities.insert(StaticBearer.id().to_owned(), Arc::new(StaticBearer));
        BridgeAppBuilder(Self {
            engines: EngineRegistry::builtin(),
            profile: Arc::new(ExplicitProfile),
            credentials: Arc::new(EnvCredentials),
            identities,
            defaults: CliDefaults::default(),
        })
    }

    pub fn engines(&self) -> &EngineRegistry { &self.engines }
    pub fn profile(&self) -> &dyn DeployProfile { self.profile.as_ref() }
    pub fn credentials(&self) -> &dyn CredentialSource { self.credentials.as_ref() }
    pub fn defaults(&self) -> CliDefaults { self.defaults }

    /// The identity resolver selected by `--provider-auth`.
    pub fn identity(&self, id: &str) -> anyhow::Result<&dyn IdentityResolver> {
        self.identities.get(id).map(|resolver| resolver.as_ref()).with_context(|| format!(
            "Unknown --provider-auth '{id}'; supported values: {}",
            self.identities.keys().map(String::as_str).collect::<Vec<_>>().join(", ")))
    }

    /// Parse the command line and run it; the process entry point of a binary.
    pub fn main(self) -> ExitCode {
        let cli = crate::cli::parse(&self);
        let result = crate::cli::prepare_process(&cli).and_then(|()| {
            let runtime = tokio::runtime::Builder::new_multi_thread().enable_all().build()
                .context("Cannot initialize bridge runtime")?;
            runtime.block_on(crate::cli::run(cli, &self))
        });
        match result {
            Ok(()) => ExitCode::SUCCESS,
            Err(error) => {
                // The source error may contain config values; print only our context.
                eprintln!("error: {error}");
                ExitCode::FAILURE
            }
        }
    }
}

pub struct BridgeAppBuilder(BridgeApp);

impl BridgeAppBuilder {
    /// Add an engine, or replace the engine registered under the same id.
    pub fn engine(mut self, factory: impl EngineFactory + 'static) -> Self {
        self.0.engines.register(factory);
        self
    }

    pub fn profile(mut self, profile: impl DeployProfile + 'static) -> Self {
        self.0.profile = Arc::new(profile);
        self
    }

    pub fn credentials(mut self, credentials: impl CredentialSource + 'static) -> Self {
        self.0.credentials = Arc::new(credentials);
        self
    }

    /// Add an identity resolver, or replace the one registered under the same id.
    pub fn identity(mut self, resolver: impl IdentityResolver + 'static) -> Self {
        self.0.identities.insert(resolver.id().to_owned(), Arc::new(resolver));
        self
    }

    pub fn defaults(mut self, defaults: CliDefaults) -> Self {
        self.0.defaults = defaults;
        self
    }

    pub fn build(self) -> BridgeApp { self.0 }
}
