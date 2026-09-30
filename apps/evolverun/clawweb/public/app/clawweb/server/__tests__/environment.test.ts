import { describe, expect, it } from "vitest";
import { resolveMachineEnvironment } from "../environment.js";

describe("resolveMachineEnvironment", () => {
  it("uses the explicit local option before process variables", () => {
    expect(resolveMachineEnvironment({ SERVER_ENV: "prod" }, "prepub")).toBe("pre");
  });

  it("uses the fixed server environment precedence", () => {
    expect(resolveMachineEnvironment({ SERVER_ENV: "gray", REAL_SERVER_ENV: "pre" })).toBe("prod");
    expect(resolveMachineEnvironment({ REAL_SERVER_ENV: "prepub", ALIPAY_APP_ENV: "prod" })).toBe("pre");
  });

  it("does not use a Bot environment to select machine configuration", () => {
    expect(resolveMachineEnvironment({ BOT_ENV: "prod", botEnv: "prod" } as NodeJS.ProcessEnv)).toBe("dev");
  });
});
