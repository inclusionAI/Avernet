mod resign;
mod verifier;
mod wire;

pub use resign::{GatewayPrincipalReAddressError, GatewayPrincipalReSigner};

pub use verifier::{
    GatewayPrincipalTokenVerifier, GatewayPrincipalTrust, GatewayPrincipalVerificationError,
    GatewayPrincipalVerifierBuildError,
};

#[cfg(test)]
mod tests;
