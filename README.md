<h1 align="center">
  <img src="./docs/images/avernet-readme-header.png" alt="Avernet" width="70%" />
</h1>

<p align="center"><strong>Avernet is an open-source infrastructure layer for building and operating persistent, coordinated, multi-agent systems at organizational scale.</strong></p>

<p align="center">Where agents live, connect, coordinate, execute, and evolve together.</p>

<p align="center">
  Works with <b>OpenClaw</b> · <b>DeepSeek Harness</b> · any runtime over the open <code>/ws/bot</code> protocol · existing bot platforms through the gateway
</p>

<p align="center">
  <a href="https://avernet.cc"><img src="https://img.shields.io/badge/Website-avernet.cc-0a7bbb.svg" alt="Website" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License" /></a>
  <a href="apps/bcs/crates/plugins/deepseek-harness-channel-bcn/README.md"><img src="https://img.shields.io/badge/DeepSeek%20Harness-plugin-4d6bfe.svg" alt="DeepSeek Harness plugin" /></a>
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/README-zh--CN-green.svg" alt="README zh-CN" /></a>
</p>

<p align="center">
  <a href="https://avernet.cc">Website</a> |
  <a href="#whats-new">What's new</a> |
  <a href="#demo">Demo</a> |
  <a href="#coherence-through-coordination">Coherence through coordination</a> |
  <a href="#quick-start">Quick Start</a> |
  <a href="#integration">Integration</a> |
  <a href="#documentation">Docs</a>
</p>

> **Production-tested at Ant Group** — As of early July 2026, Avernet supports multi-agent deployments across **12 business groups (BGs)**, with a **90%+ task completion rate in measured multi-agent workflows**.

## What's new

- **Oct 2026** · [avernet.cc](https://avernet.cc) is live, with collaboration scenarios and docs.
- **Sep 2026** · [DeepSeek Harness plugin](apps/bcs/crates/plugins/deepseek-harness-channel-bcn/README.md): connect a DSH Bot to the network with automatic onboarding, an isolated session per conversation, and manager-worker task tools. Listed in [awesome-dsh-plugin](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin).
- **Sep 2026** · Bot WebSocket protocol V3: canonical session IDs and one shared run-event contract across engines. Recommended for new integrations; V1 and V2 remain available for compatibility. See the [Bot Integration Guide](docs/bot-integration.md).
- **Aug 2026** · [v2026.08.04](https://github.com/inclusionAI/Avernet/releases/tag/v2026.08.04): coordination routing integrated into the gateway, and safer Skills Pool cutover and rollback.
- **Jul 2026** · [First open-source release](https://github.com/inclusionAI/Avernet/releases/tag/v2026.07.15).

## Demo

<p align="center">
  <video src="https://github.com/user-attachments/assets/f3fc4b52-4d23-4a73-b618-fe0110e2f2fb" width="80%" controls></video>
</p>

<p align="center">
  <img src="./docs/images/group.jpg" alt="Group coordination" width="80%" />
</p>

The public demo covers local onboarding, workbench interaction, and coordination among local test bots. It does not show production-scale properties such as large connection envelopes, permission isolation, audit depth, failure recovery, or long-horizon organizational collaboration.

## Coherence through coordination

Smarter agents do not add up to a coherent organization. When agents come from different runtimes, owners and teams, they duplicate work, act on stale context and quietly contradict each other. Avernet treats **coherence as the goal and coordination as the mechanism**. It does not replace an agent's own reasoning; it coordinates how agents find each other, share state and hand off work.

| Coherence breaks when… | Coordination in Avernet | Result |
| --- | --- | --- |
| **Cannot find**: capabilities are hard to discover | Registration and discovery across runtimes in one network | Every agent and capability is findable |
| **Cannot align**: apparent consensus hides real misalignment | Team formation, canonical sessions, and roles assigned by the server rather than inferred by the model | Agents act on one shared session state |
| **Cannot run fast**: execution depends on human relay | Routing, manager-worker task dispatch, and human-in-the-loop sessions | Work moves between agents without manual relay |
| **Cannot retain**: knowledge does not accumulate | Bot diagnosis, repeatable benchmarks and versioned optimization through [AgentEvolve](docs/agent-evolve.md); shared context and memory are planned | Improvements compound instead of resetting |

<p align="center">
  <img src="./docs/images/organizational-problems.jpg" alt="Organizational alignment problems" width="80%" />
</p>

## Quick Start

Clone the repository:

```bash
git clone https://github.com/inclusionAI/Avernet.git
cd Avernet
```

### Recommended local setup

```bash
./singlebox/singlebox.sh install-tools
./singlebox/singlebox.sh
```

This starts a local Avernet stack with:

- Avernet process
- frontend workbench
- 5 local test bots

Open the frontend at:

```text
http://127.0.0.1:8000/
```

The test bots reuse the model settings in `~/.openclaw/openclaw.json` when present. To use any OpenAI-compatible model instead, see [Model configuration](docs/quick-start.md#model-configuration).

For Docker and advanced setup options, see:

- [Quick Start](docs/quick-start.md)
- [Docker Guide](docs/docker.md)
- [Dependencies](docs/dependencies.md)

## Integration

Avernet does not lock you into a single agent engine. Ready-made plugins connect OpenClaw and DeepSeek Harness, any other runtime can join over the open `/ws/bot` protocol, and existing bot platforms can be scheduled through the gateway.

| Integration path | Best for | Current capability | Docs |
| --- | --- | --- | --- |
| Plugin integration | OpenClaw, DeepSeek Harness, local agent runtimes, custom bot processes | Agents actively connect to Avernet through a plugin or runtime for registration, onboarding, message receiving, and result reporting. | [Bot Integration Guide](docs/bot-integration.md), [Local OpenClaw from source](docs/openclaw-bcn-local.md), [DeepSeek Harness plugin](apps/bcs/crates/plugins/deepseek-harness-channel-bcn/README.md) |
| Gateway integration | Existing bot platforms, multi-instance agent services, external scheduling systems | Avernet dispatches tasks to an external platform, which schedules agents and reports results back when work completes. | [Bot Platform Integration](apps/bcs/docs/bot-provider-integration.md) |

## How Avernet fits

Avernet works alongside the protocols and frameworks you already use. Agents keep their own reasoning, tools and protocols; Avernet coordinates them at the organization level.

| | Connects | Scope |
| --- | --- | --- |
| MCP | An agent to tools and data | One agent's toolbox |
| A2A | One agent to another agent | Agent-to-agent calls |
| Agent frameworks (LangGraph, CrewAI, AutoGen, …) | Roles inside one program | One application |
| **Avernet** | Many agents, runtimes and people | One organization: discovery, teams, routing and shared sessions |

## Capabilities & Status

> **Status note:** All core capability areas are deployed internally in production environments. Public open-source coverage varies by component and is being released incrementally.
>
> **Legend:** Available = usable in the public repo now · Partial = partially public · In progress = being opened or integrated · Planned = intended but not yet public

- **Trusted core**  
  ![Identity](https://img.shields.io/badge/Identity-Available-brightgreen)
  ![Auth](https://img.shields.io/badge/Auth-Available-brightgreen)
  ![Permissions](https://img.shields.io/badge/Permissions-Partial-yellow)
  ![Security](https://img.shields.io/badge/Security-Planned-lightgrey)
  ![Audit](https://img.shields.io/badge/Audit-In%20progress-orange)
  ![Lifecycle](https://img.shields.io/badge/Lifecycle-In%20progress-orange)  
  Identity, auth, permissions, security, audit, and lifecycle management for agents and participants.

- **Execution infrastructure**  
  ![Heterogeneous runtimes](https://img.shields.io/badge/Heterogeneous%20runtimes-Available-brightgreen)
  ![Bot services](https://img.shields.io/badge/Bot%20services-Available-brightgreen)
  ![Containers](https://img.shields.io/badge/Containers-Partial-yellow)
  ![Clusters](https://img.shields.io/badge/Clusters-Planned-lightgrey)
  ![Operations](https://img.shields.io/badge/Operations-In%20progress-orange)  
  Support for heterogeneous agent engines, bot-as-a-service runtimes, containers, clusters, and operational runtimes.

- **Agent coordination network**  
  ![Discovery](https://img.shields.io/badge/Discovery-Available-brightgreen)
  ![Relationships](https://img.shields.io/badge/Relationships-Available-brightgreen)
  ![Team formation](https://img.shields.io/badge/Team%20formation-Available-brightgreen)
  ![Routing](https://img.shields.io/badge/Routing-Available-brightgreen)
  ![Collaboration](https://img.shields.io/badge/Collaboration-Available-brightgreen)
  ![Governance](https://img.shields.io/badge/Governance-Planned-lightgrey)  
  Discovery, relationship building, team formation, routing, collaboration, and governance across multiple agents.

- **Shared intelligence and evolution**  
  ![Context](https://img.shields.io/badge/Context-Planned-lightgrey)
  ![Memory](https://img.shields.io/badge/Memory-Planned-lightgrey)
  ![Orchestration](https://img.shields.io/badge/Orchestration-Available-brightgreen)
  ![Evaluation](https://img.shields.io/badge/Evaluation-Available-brightgreen)
  ![Evolution](https://img.shields.io/badge/Evolution-Available-brightgreen)

  Bot diagnosis, repeatable Bench evaluation, goal- or diagnosis-driven optimization, and recoverable Pack versions are available through [AgentEvolve](docs/agent-evolve.md). Orchestration is available via the Avernet coordination layer. Context and memory remain planned.

- **Application building blocks**  
  ![Apps](https://img.shields.io/badge/Apps-Planned-lightgrey)
  ![Canvas](https://img.shields.io/badge/Canvas-Available-brightgreen)
  ![Workflow](https://img.shields.io/badge/Workflow-Available-brightgreen)
  ![Extensions](https://img.shields.io/badge/Extensions-Planned-lightgrey)  
  Agent apps, canvas apps, workflows, and domain-specific extensions built on top of Avernet. Workflow orchestration is available through [TaskGuard](docs/taskguard.md).

## Architecture

```text
   +----------------------------+  +----------------------------+  +----------------------------+
   | Local Agents               |  | Agent Runtime              |  | Existing Bot Platform      |
   | Plugin mode                |  | /ws/bot runtime            |  | Downlink gateway           |
   +-------------+--------------+  +-------------+--------------+  +-------------+--------------+
                 |                               |                               ^
                 |                               |                               |
                 +---------------+---------------+                               |
                                 | agent -> BCS:                                 | BCS -> platform:
                                 | connect / register / receive / report         | dispatch / schedule / callback
                                 v                                               |
+----------------------------------------------------------------------------+     +-------------------+
| Avernet / BCS                                                              |     | bcs-cli / tools   |
| connection / registration / routing / delivery / sessions                  |<--->| onboard / inspect |
| collaboration state / multi-bot network management                         |     |                   |
+----------------------------------------------------------------------------+     +-------------------+
```

## Repository layout

```text
Avernet/
├── apps/
│   ├── bcs/               # Bot Coordination Service (Rust) and channel plugins
│   ├── backend/           # Backend services (Python)
│   ├── gateway/           # External API gateway
│   ├── frontend/          # Web workbench
│   ├── frontend-nextgen/  # Next-generation workbench
│   ├── evolverun/         # AgentEvolve and TaskGuard
│   └── ...                # baas, bcsfuse, proxy
├── engine/                # Engine adapter
├── middleware/            # Python and Rust middleware
├── singlebox/             # Local development stack (singlebox.sh)
├── docker/                # Container images and deployment scripts
├── docs/                  # Guides, ADRs and architecture rules
├── AGENTS.md              # Rules for contributors and AI coding agents
├── CONTEXT-MAP.md         # Entry point for architecture and module context
├── README.md
└── README.zh-CN.md
```

## Documentation

- [Quick Start](docs/quick-start.md)
- [Dependencies](docs/dependencies.md)
- [Docker Guide](docs/docker.md)
- [Bot Platform Integration](apps/bcs/docs/bot-provider-integration.md)
- [Bot Integration Guide](docs/bot-integration.md)
- [Local OpenClaw from source](docs/openclaw-bcn-local.md)
- [DeepSeek Harness plugin](apps/bcs/crates/plugins/deepseek-harness-channel-bcn/README.md)
- [Architecture docs](docs/arch/)
- [BCS Development Guide](apps/bcs/README.md)
- [AgentEvolve](docs/agent-evolve.md)
- [TaskGuard](docs/taskguard.md)

## Community

- Questions, bug reports and ideas: [GitHub Issues](https://github.com/inclusionAI/Avernet/issues)
- Project site: [avernet.cc](https://avernet.cc)

If Avernet is useful to you, a ⭐ helps more teams find it.

## Security

Do not commit secrets, tokens, cookies, private keys, private service endpoints, local databases, runtime logs, or machine-specific configuration.

If credentials have already been committed, revoke or rotate them before cleaning repository history.

## License

This project is licensed under the [Apache License 2.0](LICENSE).
