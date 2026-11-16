//! Durable deployment artifact: retain every accepted transport version until its
//! sessions are retired. Never reconstruct history from mutable management rows.
use bcn_runtime_model::*;
use serde::{Deserialize,Serialize};
use sha2::{Digest,Sha256};
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(tag="protocol",rename_all="snake_case",deny_unknown_fields)]
pub enum TransportRoute {
    A2a { card:serde_json::Value },
    Provider { endpoint:String, provider_id:String, provider_bot_ref:String, group_id:String, timeout_ms:u64 },
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FrozenTransport { pub binding:FrozenTargetBinding, pub route:TransportRoute }
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TransportManifest { pub entries:Vec<FrozenTransport> }
impl FrozenTransport {
    pub fn version(&self)->RuntimeResult<String> {
        // Recursively sorted JSON makes object key ordering irrelevant even if
        // serde_json preserve_order is enabled by another workspace consumer.
        fn canonical(v:serde_json::Value)->serde_json::Value {
            match v {
                serde_json::Value::Object(m)=>serde_json::Value::Object(m.into_iter().map(|(k,v)|(k,canonical(v))).collect::<std::collections::BTreeMap<_,_>>().into_iter().collect()),
                serde_json::Value::Array(v)=>serde_json::Value::Array(v.into_iter().map(canonical).collect()),
                v=>v,
            }
        }
        let v=serde_json::to_value((&self.binding.target,&self.binding.binding_id,&self.binding.credential_ref,&self.binding.capabilities,&self.route))
            .map_err(|_|RuntimeError::InvalidInput("transport manifest serialization".into()))?;
        let bytes=serde_json::to_vec(&canonical(v)).map_err(|_|RuntimeError::InvalidInput("transport manifest serialization".into()))?;
        Ok(format!("sha256:{:x}",Sha256::digest(bytes)))
    }
}
impl TransportManifest {
    pub fn validate(&self)->RuntimeResult<()> {
        if self.entries.is_empty() || self.entries.len()>4096 {return Err(RuntimeError::InvalidInput("transport manifest size".into()))}
        let mut seen=std::collections::BTreeSet::new();
        for e in &self.entries {
            e.binding.target.scope.validate().map_err(|_|RuntimeError::InvalidInput("transport scope".into()))?;
            if e.binding.binding_id.is_empty() || e.binding.binding_version!=e.version()?
                || !seen.insert((e.binding.binding_id.clone(),e.binding.binding_version.clone())) {
                return Err(RuntimeError::InvalidInput("ambiguous or changed frozen transport".into()));
            }
        }
        Ok(())
    }
    pub fn find(&self,binding:&FrozenTargetBinding)->RuntimeResult<&FrozenTransport> {
        self.entries.iter().find(|e|&e.binding==binding)
            .ok_or_else(||RuntimeError::Unsupported("historical transport missing from retained manifest".into()))
    }
}
