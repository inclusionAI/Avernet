import { afterEach, describe, expect, it, vi } from "vitest";
import {
  configureClawWebPublicBaseUrl,
  getClawWebPublicBaseUrl,
  getCurrentEnv,
} from "../env.js";

function clearRuntimeEnv(): void {
  vi.stubEnv("SERVER_ENV", "");
  vi.stubEnv("REAL_SERVER_ENV", "");
  vi.stubEnv("ALIPAY_APP_ENV", "");
  vi.stubEnv("CLAWWEB_PUBLIC_BASE_URL", "");
  vi.stubEnv("CLAWWEB_URL", "");
}

describe("Clawevolve environment", () => {
  afterEach(() => {
    configureClawWebPublicBaseUrl(undefined);
    vi.unstubAllEnvs();
  });

  it("uses the ClawWeb ALIPAY_APP_ENV alias", () => {
    clearRuntimeEnv();
    vi.stubEnv("ALIPAY_APP_ENV", "prepub");
    expect(getCurrentEnv()).toBe("pre");
  });

  it("falls back to dev when no ClawWeb environment exists", () => {
    clearRuntimeEnv();
    expect(getCurrentEnv()).toBe("dev");
  });

  it("uses the standard public URL outside dev", () => {
    clearRuntimeEnv();
    vi.stubEnv("SERVER_ENV", "pre");
    expect(getClawWebPublicBaseUrl()).toBe("https://clawweb-pre.alipay.com");
  });

  it("shares a custom Host origin with all ClawWeb modules", () => {
    clearRuntimeEnv();
    configureClawWebPublicBaseUrl(
      "https://custom-clawweb.example.com",
      ["https://custom-clawweb.example.com"],
    );
    expect(getClawWebPublicBaseUrl()).toBe("https://custom-clawweb.example.com");
  });
});
