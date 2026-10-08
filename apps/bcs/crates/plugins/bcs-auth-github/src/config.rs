//! GitHub OAuth endpoint constants and configuration.

/// GitHub OAuth authorization endpoint.
pub const GITHUB_AUTH_URL: &str = "https://github.com/login/oauth/authorize";

/// GitHub OAuth token exchange endpoint.
pub const GITHUB_TOKEN_URL: &str = "https://github.com/login/oauth/access_token";

/// GitHub API userinfo endpoint.
pub const GITHUB_USERINFO_URL: &str = "https://api.github.com/user";

/// OAuth scopes requested from GitHub.
pub const GITHUB_SCOPES: &str = "read:user user:email";

/// GitHub OAuth client configuration.
pub struct GitHubOAuthConfig {
    pub client_id: String,
    pub client_secret: String,
}