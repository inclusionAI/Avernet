//! Redis-compatible route type enumeration.

use std::fmt;
use std::str::FromStr;

/// Route type used by deployments that require zone-aware Redis routing.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, serde::Serialize, serde::Deserialize)]
pub enum RedisRouteType {
    /// GZone - Global cache
    #[default]
    G,
    /// CZone - Central cache
    C,
    /// RZone - Regional/Partition cache
    R,
}

impl RedisRouteType {
    /// Get route type as string
    pub const fn as_str(&self) -> &'static str {
        match self {
            RedisRouteType::G => "G",
            RedisRouteType::C => "C",
            RedisRouteType::R => "R",
        }
    }
}

impl fmt::Display for RedisRouteType {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.as_str())
    }
}

impl FromStr for RedisRouteType {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.to_uppercase().as_str() {
            "G" => Ok(RedisRouteType::G),
            "C" => Ok(RedisRouteType::C),
            "R" => Ok(RedisRouteType::R),
            _ => Err(format!("Invalid route type: {}", s)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_route_type_default() {
        let rt: RedisRouteType = Default::default();
        assert_eq!(rt, RedisRouteType::G);
    }

    #[test]
    fn test_route_type_from_str() {
        assert_eq!("G".parse::<RedisRouteType>().unwrap(), RedisRouteType::G);
        assert_eq!("C".parse::<RedisRouteType>().unwrap(), RedisRouteType::C);
        assert_eq!("R".parse::<RedisRouteType>().unwrap(), RedisRouteType::R);
        assert!("X".parse::<RedisRouteType>().is_err());
    }

    #[test]
    fn test_route_type_display() {
        assert_eq!(RedisRouteType::G.to_string(), "G");
        assert_eq!(RedisRouteType::C.to_string(), "C");
        assert_eq!(RedisRouteType::R.to_string(), "R");
    }
}
