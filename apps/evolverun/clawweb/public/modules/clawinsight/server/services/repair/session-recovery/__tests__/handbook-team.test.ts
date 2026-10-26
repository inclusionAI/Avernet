import { describe, expect, it } from "vitest";
import { resolveRecoveryHandbook } from "../handbook.js";
import type { RecoveryRequest } from "../contracts.js";

const request = { tc_fault_label: "TC.SKILL.INSTRUCTION", target: { env: "prod", bot_id: "default", entity_id: "owner-a" } } as RecoveryRequest;
const config = () => ({
  bot_teams: [{ env: "prod", bot_id: "default", owner_id: "owner-a", team_key: "team-a" }],
  team_routes: { "team-a": { "TC.SKILL": ["skill/handbook.md"], "TC.MCP": ["skill/handbook.md", "mcp/handbook.md"] } },
});
const store = (value: unknown, enabled = 1) => ({ findByKey: async () => ({ enabled, config_json: JSON.stringify(value) }) });

describe("team handbook selection", () => {
  it("selects custom business rules before common references", async () => {
    expect(await resolveRecoveryHandbook(request, store(config()))).toMatchObject({ teamKey: "team-a",
      chapters: ["custom/team-a/skill/handbook.md", "common/file-download/handbook.md"] });
    expect((await resolveRecoveryHandbook({ ...request, tc_fault_label: "TC.MCP.PERMISSION" }, store(config()))).chapters)
      .toEqual(["custom/team-a/skill/handbook.md", "custom/team-a/mcp/handbook.md", "common/mcp/handbook.md", "common/file-download/handbook.md"]);
  });
  it.each([{ env: "pre" }, { bot_id: "another" }, { entity_id: "owner-b" }])(
    "does not share a team's rules with a different target %j", patch => {
      return expect(resolveRecoveryHandbook({ ...request, target: { ...request.target, ...patch } }, store(config())))
        .resolves.toMatchObject({ teamKey: null, chapters: ["common/file-download/handbook.md"] });
    });
  it("allows several Bots in one team and retains team scope for an unmapped category", async () => {
    const value = config(); value.bot_teams.push({ ...value.bot_teams[0], bot_id: "bot-b" });
    expect(await resolveRecoveryHandbook({ ...request, tc_fault_label: "TC.ENGINE.CODE",
      target: { ...request.target, bot_id: "bot-b" } }, store(value))).toMatchObject({ teamKey: "team-a", chapters: [] });
  });
  it("uses common only for absent or explicitly disabled configuration", async () => {
    for (const source of [{ findByKey: async () => null }, store({ invalid: true }, 0)]) {
      expect(await resolveRecoveryHandbook(request, source)).toMatchObject({ teamKey: null, chapters: ["common/file-download/handbook.md"] });
    }
  });
  it.each([
    { invalid: true },
    { ...config(), bot_teams: [{ ...config().bot_teams[0], env: ["prod"] }] },
    { ...config(), bot_teams: [config().bot_teams[0], config().bot_teams[0]] },
    { ...config(), bot_teams: [{ ...config().bot_teams[0], team_key: "../team-b" }] },
    { ...config(), team_routes: {} },
    { ...config(), team_routes: { "team-a": { "TC.SKILL": ["../team-b/skill/handbook.md"] } } },
    { ...config(), team_routes: { "team-a": { "TC.SKILL": ["/skill/handbook.md"] } } },
    { ...config(), team_routes: { "team-a": { "TC.SKILL": ["skill/handbook.md", "skill/handbook.md"] } } },
    { ...config(), team_routes: { "team-a": { "TC.SKILL.INSTRUCTION": ["skill/handbook.md"] } } },
  ])("rejects ambiguous or malformed configuration before selecting common as a fallback: %j", async value => {
    await expect(resolveRecoveryHandbook(request, store(value))).rejects.toMatchObject({ status: 503, code: "handbook_configuration_invalid" });
  });
  it("propagates storage failures and invalid enabled states", async () => {
    await expect(resolveRecoveryHandbook(request, { findByKey: async () => { throw new Error("database unavailable"); } }))
      .rejects.toThrow("database unavailable");
    await expect(resolveRecoveryHandbook(request, store(config(), 2))).rejects.toMatchObject({ status: 503 });
  });
});
