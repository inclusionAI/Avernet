//! WeChat OAuth provider for BCS.
//!
//! Implements the WeChat-specific [`OAuthProvider`](bcs_auth_api::OAuthProvider)
//! (token exchange + userinfo). Session verification is provider-agnostic and
//! handled by `bcs_auth_api::OAuthSessionPlugin`, so this crate carries no
//! `AuthPlugin`.

pub mod config;
pub mod provider;

pub use config::WeChatConfig;
pub use provider::WeChatOAuthProvider;