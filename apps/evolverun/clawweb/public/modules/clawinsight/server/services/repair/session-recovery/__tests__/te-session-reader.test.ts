import { expect, it, vi } from "vitest";
import { RecoverySessionReader } from "../session-reader.js";
const target = { environment: "prod" as const, ownerId: "owner", botId: "bot", provider: "teclaw", bindingId: "1", deviceId: "device", activeEngine: "teclaw" };
it("rejects TE session reads locally without requesting the session API or Shell", async () => {
  const runtime = { request: vi.fn(), executeShell: vi.fn() };
  await expect(new RecoverySessionReader(runtime).read({ target, sessionKey: "original", deliveryKey: "send" }))
    .rejects.toMatchObject({ status: 422, code: "unsupported_session_read", message: expect.stringContaining("trajectory_read") });
  expect(runtime.request).not.toHaveBeenCalled(); expect(runtime.executeShell).not.toHaveBeenCalled();
});
