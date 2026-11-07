//! Explicit credential guarantees. Wire format alone is not a trust profile.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CallerCredentialProfile {
    /// Fresh proof, bounded by input deadlines, with exact execution verification.
    ExecutionBound,
    /// Upstream Caller identity credential; may be reused and outlive a BCS run.
    /// BCS owns execution authorization; downstream owns identity verification.
    IdentityRelay,
}

/// Bounded upstream machine code, never a free-form message or response body.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub struct CallerIdentityRejectionCode {
    bytes: [u8; 64],
    len: u8,
}

impl CallerIdentityRejectionCode {
    pub fn new(code: &str) -> Option<Self> {
        if code.is_empty() || code.len() > 64 || !code.contains('_')
            || !code.bytes().all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_') {
            return None;
        }
        let mut bytes = [0; 64];
        bytes[..code.len()].copy_from_slice(code.as_bytes());
        Some(Self { bytes, len: code.len() as u8 })
    }
}

impl std::fmt::Display for CallerIdentityRejectionCode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        // Constructor accepts ASCII only; fields are private.
        f.write_str(std::str::from_utf8(&self.bytes[..self.len as usize]).unwrap_or("INVALID_CODE"))
    }
}
impl std::fmt::Debug for CallerIdentityRejectionCode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result { std::fmt::Display::fmt(self, f) }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn machine_codes_only() {
        assert_eq!(CallerIdentityRejectionCode::new("CALLER_EXPIRED").unwrap().to_string(), "CALLER_EXPIRED");
        for bad in ["", "eyJ.secret.jwt", "Bearer secret", "CALLER_bad", "CALLER_\nSECRET", &"X".repeat(65)] {
            assert!(CallerIdentityRejectionCode::new(bad).is_none());
        }
    }
}
