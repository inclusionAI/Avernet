use bcs_domain::{
    BindingChannel, BindingChannels, BotCapabilities, BotDynamicStatus, DynamicStatusResponse,
    Skill,
};
use bcs_protocol as wire;

pub fn to_core_skill(skill: wire::Skill) -> Skill {
    Skill {
        name: skill.name,
        description: skill.description,
    }
}

pub fn to_wire_skill(skill: Skill) -> wire::Skill {
    wire::Skill {
        name: skill.name,
        description: skill.description,
    }
}

pub fn to_core_binding_channels(
    channels: wire::BindingChannels,
) -> BindingChannels {
    channels
        .into_iter()
        .map(|(name, channel)| {
            (
                name,
                BindingChannel {
                    binding_key: channel.binding_key,
                },
            )
        })
        .collect()
}

pub fn to_wire_binding_channels(
    channels: BindingChannels,
) -> wire::BindingChannels {
    channels
        .into_iter()
        .map(|(name, channel)| {
            (
                name,
                wire::BindingChannel {
                    binding_key: channel.binding_key,
                },
            )
        })
        .collect()
}

pub fn to_core_capabilities(capabilities: wire::BotCapabilities) -> BotCapabilities {
    BotCapabilities {
        name: capabilities.name,
        summary: capabilities.summary,
        domains: capabilities.domains,
        skills: capabilities.skills.into_iter().map(to_core_skill).collect(),
        scopes: capabilities.scopes,
        binding_channels: capabilities.binding_channels.map(to_core_binding_channels),
        hidden: capabilities.hidden,
        visibility: capabilities.visibility,
        agent_code: None,
        agent_token: None,
    }
}

pub fn to_wire_capabilities(capabilities: BotCapabilities) -> wire::BotCapabilities {
    wire::BotCapabilities {
        name: capabilities.name,
        summary: capabilities.summary,
        domains: capabilities.domains,
        skills: capabilities.skills.into_iter().map(to_wire_skill).collect(),
        scopes: capabilities.scopes,
        binding_channels: capabilities.binding_channels.map(to_wire_binding_channels),
        hidden: capabilities.hidden,
        visibility: capabilities.visibility,
    }
}

pub fn to_core_dynamic_status(status: wire::BotDynamicStatus) -> BotDynamicStatus {
    BotDynamicStatus {
        status: status.status,
        dynamic_summary: status.dynamic_summary,
        load: status.load,
        updated_at: status.updated_at,
    }
}

pub fn to_wire_dynamic_status(status: BotDynamicStatus) -> wire::BotDynamicStatus {
    wire::BotDynamicStatus {
        status: status.status,
        dynamic_summary: status.dynamic_summary,
        load: status.load,
        updated_at: status.updated_at,
    }
}

pub fn to_wire_dynamic_status_response(
    status: DynamicStatusResponse,
) -> wire::DynamicStatusResponse {
    wire::DynamicStatusResponse {
        status: status.status,
    }
}
