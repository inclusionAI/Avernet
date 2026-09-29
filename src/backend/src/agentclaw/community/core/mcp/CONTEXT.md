# AgentClaw MCP Configuration

The configuration language for a user's MCP defaults and a Bot's effective MCP connection settings.

## Language

**User MCP Default**:
The MCP connection settings supplied by a user for their Bots to inherit when those settings are not overridden for a Bot.
_Avoid_: All-Bot override

**Bot MCP Header Override**:
A Header parameter explicitly assigned to one Bot; for the same Header name, the Bot's value takes precedence over the User MCP Default. Other Header names continue to inherit their user defaults.
_Avoid_: Complete Bot Header snapshot

**Explicit Bot Header Set**:
The collection of Header parameters explicitly assigned to a Bot, independently of the Header parameters it inherits from the user.
_Avoid_: Effective MCP Headers

**Effective MCP Configuration**:
The MCP connection settings a particular Bot uses after its explicit settings and inherited defaults have been resolved.
_Avoid_: Stored configuration

**MCP Parameter Group**:
A named Header parameter, its value, and the set of Bots to which that value is explicitly assigned; an empty Bot set denotes a user default.
_Avoid_: MCP installation
