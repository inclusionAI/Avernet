use std::convert::Infallible;

use axum::extract::FromRequestParts;
use axum::http::request::Parts;
use axum::routing::MethodRouter;
use axum::Extension;
use bcs_service_api::application::v1::IdentityPolicy;

/// Axum route annotation for selecting an OpenAPI effective Actor policy.
pub trait IdentityPolicyMethodRouterExt<S> {
    fn identity_policy(self, policy: IdentityPolicy) -> Self;
}

impl<S> IdentityPolicyMethodRouterExt<S> for MethodRouter<S>
where
    S: Clone + Send + Sync + 'static,
{
    fn identity_policy(self, policy: IdentityPolicy) -> Self {
        self.layer(Extension(policy))
    }
}

/// Extracts a route's explicit policy or fails closed as `HumanOnly`.
pub struct RouteIdentityPolicy(pub IdentityPolicy);

impl<S> FromRequestParts<S> for RouteIdentityPolicy
where
    S: Send + Sync,
{
    type Rejection = Infallible;

    async fn from_request_parts(
        parts: &mut Parts,
        _state: &S,
    ) -> Result<Self, Self::Rejection> {
        Ok(Self(
            parts
                .extensions
                .get::<IdentityPolicy>()
                .copied()
                .unwrap_or_default(),
        ))
    }
}

#[cfg(test)]
mod tests {
    use axum::extract::FromRequestParts;
    use axum::http::Request;
    use bcs_service_api::application::v1::IdentityPolicy;

    use super::RouteIdentityPolicy;

    #[tokio::test]
    async fn missing_route_annotation_defaults_to_human_only() {
        let (mut parts, _) = Request::new(()).into_parts();

        let RouteIdentityPolicy(policy) =
            RouteIdentityPolicy::from_request_parts(&mut parts, &())
                .await
                .expect("infallible policy extraction");

        assert_eq!(policy, IdentityPolicy::HumanOnly);
    }

    #[tokio::test]
    async fn explicit_route_annotation_overrides_the_default() {
        let (mut parts, _) = Request::new(()).into_parts();
        parts.extensions.insert(IdentityPolicy::HumanOrOwnedBot);

        let RouteIdentityPolicy(policy) =
            RouteIdentityPolicy::from_request_parts(&mut parts, &())
                .await
                .expect("infallible policy extraction");

        assert_eq!(policy, IdentityPolicy::HumanOrOwnedBot);
    }
}
