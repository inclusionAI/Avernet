/** Compatibility type for existing consumers; the runtime implementation is injected by the Host. */
export type {
  AisExecutor as AistudioService,
  AisStatus as AistudioStatus,
  AisJobStatusDetail as AistudioJobStatusDetail,
} from "@avernet/clawevolve/server/contracts/ais-executor";
