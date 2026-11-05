//! Card authentication alternatives and deployment policy, independent of transport.
use serde_json::Value;

/// Frozen anonymous choice in the existing run credential-kind column.
/// It is never passed to a credential repository as a lookup key.
pub const ANONYMOUS: &str = "a2a_anonymous";
pub const PROVIDER_BEARER: &str = "downlink_bcs_to_provider";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum A2aAuthentication { Anonymous, ProviderBearer }
impl A2aAuthentication {
    pub fn credential_kind(self)->&'static str {
        match self {Self::Anonymous=>ANONYMOUS,Self::ProviderBearer=>PROVIDER_BEARER}
    }
}

/// Select only an explicitly satisfied alternative. Unknown requirements never
/// become anonymous; AND requirements cannot be satisfied by one bearer alone.
pub fn select_authentication(card:&Value,allow_anonymous:bool,bearer_ready:bool)
    ->Result<A2aAuthentication,&'static str> {
    if !card.is_object() {return Err("invalid Card");}
    let requirements=match card.get("securityRequirements") {
        None=>None,
        Some(Value::Array(values))=>Some(values),
        _=>return Err("invalid security requirements"),
    };
    if requirements.is_none_or(|values|values.is_empty()) {
        return if allow_anonymous {Ok(A2aAuthentication::Anonymous)} else {Err("anonymous A2A access is not enabled")};
    }
    let mut bearer_supported=false;
    for requirement in requirements.into_iter().flatten() {
        // ProtoJSON omits an empty schemes map. An empty requirement is the
        // anonymous OR alternative, just like an explicit {"schemes":{}}.
        if requirement.as_object().is_some_and(|value|value.is_empty()) {
            if allow_anonymous {return Ok(A2aAuthentication::Anonymous);}
            continue;
        }
        let schemes=requirement.get("schemes").and_then(Value::as_object).ok_or("invalid security requirement")?;
        if schemes.is_empty() && allow_anonymous {return Ok(A2aAuthentication::Anonymous);}
        if schemes.len()!=1 {continue;}
        let (name,scopes)=schemes.iter().next().ok_or("invalid security requirement")?;
        let scopes=scopes.as_object().ok_or("invalid security scopes")?;
        // ProtoJSON omits empty repeated fields; an empty StringList is valid.
        match scopes.get("list") {
            None=>{},
            Some(Value::Array(values)) if values.is_empty()=>{},
            Some(Value::Array(_))=>continue,
            _=>return Err("invalid security scopes"),
        }
        let scheme=card.get("securitySchemes").and_then(|v|v.get(name))
            .and_then(|v|v.get("httpAuthSecurityScheme")).and_then(|v|v.get("scheme")).and_then(Value::as_str);
        if scheme.is_some_and(|v|v.eq_ignore_ascii_case("bearer")) {bearer_supported=true;}
    }
    if bearer_supported && bearer_ready {Ok(A2aAuthentication::ProviderBearer)}
    else {Err("A2A authentication requirements cannot be satisfied")}
}
