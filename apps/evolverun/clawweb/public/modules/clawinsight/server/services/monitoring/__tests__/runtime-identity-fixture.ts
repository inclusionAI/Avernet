/** Synthetic credentials only; deliberately includes an empty value and an extension. */
export const runtimeIdentity = {
  TOKEN: "dummy", CLIENT_ID: `DEVICE-${"a".repeat(32)}`,
  OWNER_ID: "example-owner", BOT_ID: "default", ENTITY_ID: "different-entity", ENTITY_TYPE: "staff",
  BOT_TYPE: "service", AGENT_CODE: "example-service", ADMINS: "", STAGE: "online", VERSION: "V16",
  EXTENSION: "中文=a=b $HOME $(not-executed)",
};
