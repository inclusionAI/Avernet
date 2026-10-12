//! BCS authentication plugin contract.
//!
//! Defines the `AuthPlugin` trait, the priority-ordered `AuthPluginChain`,
//! shared types, outbound ports, config, and the OAuth provider/session
//! contracts. Concrete plugin implementations live in `crates/plugins/bcs-auth-*`;
//! Noop/Mock doubles live in `bcs-test-support`.

pub mod types;
pub mod actor;
pub mod credentials;
pub use credentials::validate_bot_credentials;
pub mod jwt;
pub mod chain;
pub mod port;
pub mod config;
pub mod cookie;
pub mod oauth_types;
pub mod oauth_provider;

pub use chain::{AuthPlugin, AuthPluginChain};
pub use actor::{BCN_ACTOR_BOT_ID_HEADER, expected_actor_id, validate_actor};
pub use config::{AuthConfig, LocalAuthConfig, OAuthConfig};
pub use cookie::{extract_session_cookie, BCS_SESSION_COOKIE};
pub use jwt::is_jwt_format;
pub use oauth_provider::OAuthProvider;
pub use oauth_types::{OAuthError, OAuthToken, ProviderUserInfo};
pub use port::{BotInfo, UserIdentityInfo, BotLookupPort, UserIdentityPort};
pub use types::{AuthError, AuthPrincipal, AuthResult, AuthSource};
