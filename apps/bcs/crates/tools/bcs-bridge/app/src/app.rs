//! Application assembly: the plugins and defaults a `bcs-bridge` binary runs with.
//!
//! [`BridgeApp::builder`] starts from the open-source defaults; a distribution
//! adds engines and identity resolvers, or replaces the deploy profile,
//! credential source and CLI defaults, then calls [`BridgeApp::main`].

use std::{collections::{BTreeMap, BTreeSet}, process::ExitCode, sync::Arc};

use anyhow::Context;
use bcs_bridge_core::engine::{EngineFactory, EngineRegistry};

use crate::credentials::{CredentialSource, EnvCredentials};
use crate::identity::{IdentityResolver, StaticBearer};
use crate::profile::{DeployProfile, ExplicitProfile};

/// Deployment initializer selected at the binary composition root. It owns
/// engine-specific filesystem configuration, without constructing an engine.
pub type EngineInitializer = fn(&std::path::Path, &toml::Table) -> anyhow::Result<String>;

/// Help text and argument defaults of the binary.
#[derive(Clone, Copy)]
pub struct CliDefaults {
    pub about: &'static str,
    /// Default `register --engine`.
    pub engine: &'static str,
    /// Default `register --model`; none leaves the model to the engine.
    pub model: Option<&'static str>,
    /// Legacy registration resolver used only when both CLI override and Token auth_mode are absent.
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
    registration_api: crate::registration::RegistrationApi,
    engines: EngineRegistry,
    profile: Arc<dyn DeployProfile>,
    credentials: Arc<dyn CredentialSource>,
    identities: BTreeMap<String, Arc<dyn IdentityResolver>>,
    defaults: CliDefaults,
    registration_identities: BTreeMap<String, String>,
    registration_permission_modes: BTreeMap<String, String>,
    engines_without_model_default: BTreeSet<String>,
    initializers: BTreeMap<String, EngineInitializer>,
    deinitializers: BTreeMap<String, EngineInitializer>,
}

impl BridgeApp {
    /// Builder preloaded with the open-source defaults: the built-in engines,
    /// [`ExplicitProfile`], [`EnvCredentials`] and the [`StaticBearer`] identity.
    pub fn builder() -> BridgeAppBuilder {
        let mut identities: BTreeMap<String, Arc<dyn IdentityResolver>> = BTreeMap::new();
        identities.insert(StaticBearer.id().to_owned(), Arc::new(StaticBearer));
        let mut engines = EngineRegistry::builtin();
        if let Some(home) = std::env::var_os("CODEX_HOME") {
            engines.register(bcs_bridge_core::engine::codex_desktop::CodexDesktopFactory::with_codex_home(home.into()));
        }
        BridgeAppBuilder(Self {
            registration_api: crate::registration::RegistrationApi::default(),
            engines,
            profile: Arc::new(ExplicitProfile),
            credentials: Arc::new(EnvCredentials),
            identities,
            defaults: CliDefaults::default(),
            registration_identities: BTreeMap::new(),
            registration_permission_modes: BTreeMap::new(),
            engines_without_model_default: BTreeSet::new(),
            initializers: BTreeMap::new(),
            deinitializers: BTreeMap::new(),
        })
    }

    pub fn registration_api(&self) -> crate::registration::RegistrationApi { self.registration_api }

    pub fn engines(&self) -> &EngineRegistry { &self.engines }
    pub fn profile(&self) -> &dyn DeployProfile { self.profile.as_ref() }
    pub fn credentials(&self) -> &dyn CredentialSource { self.credentials.as_ref() }
    pub fn defaults(&self) -> CliDefaults { self.defaults }

    /// Whether this engine uses the distribution's CLI model default.
    pub fn uses_registration_model_default(&self, engine: &str) -> bool {
        !self.engines_without_model_default.contains(engine)
    }

    /// Permission mode written for a newly registered Bot using this engine.
    /// None leaves the permission mode to the engine's own defaults.
    pub fn registration_permission_mode(&self, engine: &str) -> Option<&str> {
        self.registration_permission_modes.get(engine).map(String::as_str)
    }

    pub fn initialize(&self, engine: &str, path: &std::path::Path, options: &toml::Table) -> anyhow::Result<String> {
        let initialize = self.initializers.get(engine)
            .with_context(|| format!("No initializer registered for engine '{engine}'"))?;
        initialize(path, options)
    }

    pub fn deinitialize(&self, engine: &str, path: &std::path::Path, options: &toml::Table) -> anyhow::Result<String> {
        let cleanup = self.deinitializers.get(engine)
            .with_context(|| format!("No deinitializer registered for engine '{engine}'"))?;
        cleanup(path, options)
    }

    /// Resolve a persisted runtime identity policy.
    pub fn identity(&self, id: &str) -> anyhow::Result<&dyn IdentityResolver> {
        self.identities.get(id).map(|resolver| resolver.as_ref()).with_context(|| format!(
            "Identity policy '{id}' is unavailable in this bridge; supported values: {}",
            self.identities.keys().map(String::as_str).collect::<Vec<_>>().join(", ")))
    }

    /// Select credential acquisition for the Provider mode declared by the token.
    pub fn registration_identity(&self, auth_mode: &str) -> anyhow::Result<&dyn IdentityResolver> {
        let id = self.registration_identities.get(auth_mode).map(String::as_str)
            .unwrap_or(if matches!(auth_mode, "static_bearer" | "provider_admin") { "static-bearer" } else { auth_mode });
        self.identity(id)
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
    /// Select a distribution's registration route and success response format.
    pub fn registration_api(mut self, api: crate::registration::RegistrationApi) -> Self {
        self.0.registration_api = api;
        self
    }

    /// Add an engine, or replace the engine registered under the same id.
    pub fn engine(mut self, factory: impl EngineFactory + 'static) -> Self {
        self.0.engines.register(factory);
        self
    }

    pub fn initializer(mut self, engine: &str, initialize: EngineInitializer) -> Self {
        self.0.initializers.insert(engine.to_owned(), initialize);
        self
    }

    pub fn deinitializer(mut self, engine: &str, cleanup: EngineInitializer) -> Self {
        self.0.deinitializers.insert(engine.to_owned(), cleanup);
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

    /// Bind a Provider wire mode to a distribution-specific credential resolver.
    pub fn registration_identity(mut self, auth_mode: impl Into<String>, resolver: impl Into<String>) -> Self {
        self.0.registration_identities.insert(auth_mode.into(), resolver.into());
        self
    }

    pub fn defaults(mut self, defaults: CliDefaults) -> Self {
        self.0.defaults = defaults;
        self
    }

    /// Set a registration permission default for one engine id.
    /// Existing runtime configurations are unaffected.
    pub fn registration_permission_mode(mut self, engine: impl Into<String>, mode: impl Into<String>) -> Self {
        self.0.registration_permission_modes.insert(engine.into(), mode.into());
        self
    }

    /// Leave model selection to this engine instead of the distribution default.
    /// An explicit --model remains available. Existing configurations are unaffected.
    pub fn without_registration_model_default(mut self, engine: impl Into<String>) -> Self {
        self.0.engines_without_model_default.insert(engine.into());
        self
    }

    pub fn build(self) -> BridgeApp { self.0 }
}
