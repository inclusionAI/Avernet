//! Pinned Structured Input exec wire profile. No remote routing authority.
use std::collections::BTreeMap;
use serde_json::{json, Map, Value};

pub const EXTENSION:&str="https://github.com/a2aproject/experimental-ext-structured-input/tree/main/v1";
pub const PROFILE:&str="https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/structured-input-exec/v1.md";
const DRAFT:&str="https://json-schema.org/draft/2020-12/schema";
type Result<T>=std::result::Result<T,&'static str>;

pub fn compatible_params(params:&Value)->bool {
    params.get("jsonschema_draft").and_then(Value::as_str)==Some("2020-12")
        && params.get("bcs_exec_profile").and_then(Value::as_str)==Some(PROFILE)
}

#[derive(Clone,Debug,PartialEq,Eq)]
pub enum Decision { AllowOnce, Deny }
impl Decision {
    pub fn as_str(&self)->&'static str {match self {Self::AllowOnce=>"allow_once",Self::Deny=>"deny"}}
    fn parse(value:&Value)->Result<Self> {match value.as_str() {
        Some("allow_once")=>Ok(Self::AllowOnce),Some("deny")=>Ok(Self::Deny),_=>Err("unsupported exec decision"),
    }}
}

#[derive(Clone,Debug,PartialEq)]
pub struct ExecTool {pub name:String,pub args:Value,pub args_sha256:String}

#[derive(Clone,Debug,PartialEq)]
pub struct ExecInput {
    pub id:String,
    pub expires_at_ms:u64,
    pub tools:BTreeMap<String,ExecTool>,
    pub accepted:BTreeMap<String,Decision>,
    /// Preserve the complete schema; UI projections never replace this snapshot.
    pub schema:Value,
}

fn object(value:&Value)->Result<&Map<String,Value>> {value.as_object().ok_or("exec object required")}
fn field<'a>(value:&'a Value,key:&str)->Result<&'a Value> {value.get(key).ok_or("missing exec field")}
fn string(value:&Value)->Result<&str> {
    value.as_str().filter(|v|!v.trim().is_empty() && v.len()<=4096).ok_or("invalid exec string")
}
fn keys(value:&Value,allowed:&[&str])->Result<()> {
    if object(value)?.keys().any(|key|!allowed.contains(&key.as_str())) {return Err("unsupported exec schema keyword");} Ok(())
}
fn equal(value:&Value,key:&str,expected:Value)->Result<()> {
    if field(value,key)?!=&expected {return Err("unsupported exec schema shape");} Ok(())
}
/// ProtoJSON Struct uses doubles; require exact safe positive integral values.
fn integer(value:&Value)->Result<u64> {
    let n=value.as_f64().ok_or("exec integer required")?;
    if !n.is_finite() || n<1.0 || n.fract()!=0.0 || n>9_007_199_254_740_991.0 {return Err("invalid exec integer");}
    Ok(n as u64)
}
fn decisions(value:&Value,tools:&BTreeMap<String,ExecTool>)->Result<BTreeMap<String,Decision>> {
    object(value)?.iter().map(|(id,value)| {
        if !tools.contains_key(id) {return Err("unknown exec tool");}
        Ok((id.clone(),Decision::parse(value)?))
    }).collect()
}

impl ExecInput {
    /// Called only for INPUT_REQUIRED after Card/profile negotiation. Task and
    /// context come from the trusted correlation, never from message metadata.
    pub fn decode(message:&a2a::Message,task:&str,context:&str,now:u64)->Result<Self> {
        let raw=serde_json::to_value(message).map_err(|_|"invalid exec message")?;
        if message.role!=a2a::Role::Agent || raw.get("taskId").and_then(Value::as_str)!=Some(task)
            || raw.get("contextId").and_then(Value::as_str)!=Some(context) {return Err("exec identity mismatch");}
        let mut schema=None;
        for part in raw.get("parts").and_then(Value::as_array).ok_or("missing exec parts")? {
            let Some(tag)=part.get("metadata").and_then(|m|m.get(EXTENSION)) else {continue;};
            match tag.get("type").and_then(Value::as_str) {
                Some("input_schema")=> {
                    if schema.is_some() {return Err("multiple pending exec schemas");}
                    keys(tag,&["type","id"])?;
                    schema=Some(Self::from_schema(string(field(tag,"id")?)?,field(part,"data")?,now)?);
                }
                // An accompanying Basic validation error is informational; it
                // grants no decision and cannot substitute for an input schema.
                Some("validation_error")=> {
                    if part.get("data").and_then(|d|d.get("valid"))!=Some(&json!(false)) {
                        return Err("invalid exec validation error");
                    }
                }
                _=>return Err("unsupported Structured Input part"),
            }
        }
        schema.ok_or("exec input schema required")
    }

    fn from_schema(id:&str,schema:&Value,now:u64)->Result<Self> {
        if serde_json::to_vec(schema).map_err(|_|"invalid exec schema")?.len()>240_000 {return Err("exec schema too large");}
        keys(schema,&["$schema","type","additionalProperties","required","properties","x-bcs-exec","title","description"])?;
        equal(schema,"$schema",json!(DRAFT))?;equal(schema,"type",json!("object"))?;
        equal(schema,"additionalProperties",json!(false))?;equal(schema,"required",json!(["decisions"]))?;
        let props=field(schema,"properties")?;keys(props,&["decisions"])?;
        let choices=field(props,"decisions")?;
        keys(choices,&["type","minProperties","additionalProperties","properties","title","description"])?;
        equal(choices,"type",json!("object"))?;equal(choices,"additionalProperties",json!(false))?;
        if integer(field(choices,"minProperties")?)?!=1 {return Err("invalid exec minimum decisions");}
        let bindings=field(schema,"x-bcs-exec")?;
        keys(bindings,&["profile","version","expiresAtMs","tools","accepted"])?;
        equal(bindings,"profile",json!(PROFILE))?;
        if integer(field(bindings,"version")?)?!=1 {return Err("unsupported exec profile version");}
        let expires_at_ms=integer(field(bindings,"expiresAtMs")?)?;
        if expires_at_ms<=now {return Err("exec input expired");}
        let raw_tools=object(field(bindings,"tools")?)?;
        if raw_tools.is_empty() || raw_tools.len()>64 {return Err("invalid exec tool count");}
        let mut tools=BTreeMap::new();
        for (id,tool) in raw_tools {
            string(&json!(id))?;keys(tool,&["name","args","argsSha256"])?;
            let args=field(tool,"args")?;object(args)?;
            if serde_json::to_vec(args).map_err(|_|"invalid exec arguments")?.len()>131_072 {return Err("exec arguments too large");}
            let digest=string(field(tool,"argsSha256")?)?;
            if digest.len()!=64 || !digest.bytes().all(|c|c.is_ascii_digit() || (b'a'..=b'f').contains(&c)) {
                return Err("invalid exec argument binding");
            }
            tools.insert(id.clone(),ExecTool {name:string(field(tool,"name")?)?.into(),args:args.clone(),args_sha256:digest.into()});
        }
        let accepted=decisions(field(bindings,"accepted")?,&tools)?;
        let offered=object(field(choices,"properties")?)?;
        if offered.len()!=tools.len() || tools.keys().any(|id|!offered.contains_key(id)) {return Err("exec schema tool mismatch");}
        for (id,choice) in offered {
            keys(choice,&["type","enum","const","title","description"])?;equal(choice,"type",json!("string"))?;
            if let Some(decision)=accepted.get(id) {
                equal(choice,"const",json!(decision.as_str()))?;
                if choice.get("enum").is_some() {return Err("conflicting accepted exec choice");}
            } else {
                let values=field(choice,"enum")?.as_array().ok_or("exec enum required")?;
                if values.len()!=2 || !values.contains(&json!("allow_once")) || !values.contains(&json!("deny"))
                    || choice.get("const").is_some() {return Err("unsupported exec choices");}
            }
        }
        Ok(Self {id:id.into(),expires_at_ms,tools,accepted,schema:schema.clone()})
    }

    /// An update can narrow time and accumulate immutable decisions, never
    /// silently change the operation that a displayed card approves.
    pub fn check_update(&self,next:&Self)->Result<()> {
        if self.id!=next.id || self.tools!=next.tools || next.expires_at_ms>self.expires_at_ms
            || self.accepted.iter().any(|(id,value)|next.accepted.get(id)!=Some(value)) {
            return Err("exec request binding changed");
        } Ok(())
    }

    pub fn reply(&self,tool:&str,decision:Decision,message_id:&str,task:&str,context:&str,now:u64)->Result<a2a::Message> {
        if now>=self.expires_at_ms || !self.tools.contains_key(tool)
            || self.accepted.get(tool).is_some_and(|previous|previous!=&decision) {return Err("invalid exec resolution");}
        string(&json!(message_id))?;string(&json!(task))?;string(&json!(context))?;
        serde_json::from_value(json!({"messageId":message_id,"taskId":task,"contextId":context,"role":"ROLE_USER",
            "parts":[{"data":{"decisions":{(tool):decision.as_str()}},"metadata":{(EXTENSION):{"type":"structured_input","id":self.id}}}]}))
            .map_err(|_|"cannot encode exec resolution")
    }

    /// Install only after validating a persisted remote acceptance receipt.
    pub fn with_receipt(&self,tool:&str,decision:Decision)->Result<Self> {
        if !self.tools.contains_key(tool) || self.accepted.get(tool).is_some_and(|v|v!=&decision) {
            return Err("conflicting exec acceptance");
        }
        let mut next=self.clone();next.accepted.insert(tool.into(),decision.clone());
        next.schema["x-bcs-exec"]["accepted"][tool]=json!(decision.as_str());
        let choice=&mut next.schema["properties"]["decisions"]["properties"][tool];
        choice.as_object_mut().ok_or("invalid stored exec choice")?.remove("enum");
        choice["const"]=json!(decision.as_str());Ok(next)
    }

    pub fn message(&self,task:&str,context:&str)->Result<a2a::Message> {
        serde_json::from_value(json!({"messageId":format!("schema-{}",self.id),"role":"ROLE_AGENT","taskId":task,"contextId":context,
            "parts":[{"data":self.schema,"metadata":{(EXTENSION):{"type":"input_schema","id":self.id}}}]}))
            .map_err(|_|"cannot encode exec schema")
    }

    /// A Task-store receipt proves acceptance, not tool execution. Require the
    /// exact request/tool/decision; unrelated metadata and text are insufficient.
    pub fn accepted_by_task(&self,metadata:&Value,tool:&str,decision:&Decision)->Result<bool> {
        if !self.tools.contains_key(tool) {return Err("unknown exec tool");}
        let Some(proof)=metadata.get(PROFILE) else {return Ok(false);};
        if integer(field(proof,"version")?)?!=1 {return Err("unsupported exec receipt version");}
        let requests=object(field(proof,"requests")?)?;
        if requests.len()>64 || serde_json::to_vec(proof).map_err(|_|"invalid exec receipt")?.len()>240_000 {return Err("exec receipt too large");}
        let Some(request)=requests.get(&self.id) else {return Ok(false);};
        let accepted=decisions(field(request,"accepted")?,&self.tools)?;
        if self.accepted.iter().any(|(id,value)|accepted.get(id)!=Some(value)) {return Err("exec receipt lost prior acceptance");}
        match accepted.get(tool) {
            Some(actual) if actual==decision=>Ok(true),Some(_)=>Err("conflicting exec acceptance"),None=>Ok(false),
        }
    }
}
