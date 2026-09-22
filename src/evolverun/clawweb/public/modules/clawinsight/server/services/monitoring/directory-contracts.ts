import type { DiagnosisPage, MonitoringSummary, MonitoringTarget, MonitoringWindow } from './contracts.js';

/** Plugin API: host-verified identity. No request fields are trusted by the domain service. */
export type MonitoringPrincipal = {
  staffId: string; isAuthenticated: boolean; isClawInsightAdmin: boolean;
  tenant: string; allowedTargetEnvs: readonly string[];
};
export type DirectoryScope = { tenant: string; allowedTargetEnvs: readonly string[] };
export type DirectoryBot = MonitoringTarget & { directoryId: string; botName: string; ownerId: string; ownerName: string | null };
export type BotScope = 'mine' | 'monitored' | 'all';
export type DirectorySearch = {
  principal: MonitoringPrincipal; scope: BotScope; q: string; limit: number; after: string | null;
};
/** Read-only directory, lazy startup, bounded queries. Not collaborator access. */
export interface MonitoringBotDirectory {
  exact(botId: string, scope: DirectoryScope): Promise<DirectoryBot[]>;
  get(target: MonitoringTarget, scope: DirectoryScope): Promise<DirectoryBot | null>;
  search(query: DirectorySearch): Promise<{ items: DirectoryBot[]; hasMore: boolean }>;
}
export type LegacyReportBinding = { reportedBotId: string; target: MonitoringTarget };
export interface MonitoringTargetResolver {
  resolve(reportedBotId: string): Promise<MonitoringTarget>;
  legacyId(target: MonitoringTarget): Promise<string | null>;
}
/** Opaque references are identifiers, NOT capabilities. ACL is reapplied on every read. */
export interface MonitoringReferences {
  encode(target: MonitoringTarget, tenant: string): string;
  decode(ref: string, tenant: string): MonitoringTarget;
  cursor(after: string, context: string): string;
  readCursor(cursor: string, context: string): string;
}
export type BotOption = {
  botRef: string; botName: string; botId: string; ownerId: string; env: string;
  enrollmentState: 'ENROLLED' | 'NOT_ENROLLED'; monitoring: MonitoringSummary | null;
  capabilities: { canView: true; canRequestEnrollment: boolean };
};
export type BotOptionsPage = { items: BotOption[]; nextCursor: string | null; window: MonitoringWindow };
/** Service API: authenticated UI reads and non-mutating enrollment placeholder. */
export interface MonitoringBrowserApi {
  options(principal: MonitoringPrincipal, query: Record<string, unknown>): Promise<BotOptionsPage>;
  status(principal: MonitoringPrincipal, ref: string, query: Record<string, unknown>): Promise<BotOption>;
  diagnoses(principal: MonitoringPrincipal, ref: string, query: Record<string, unknown>): Promise<DiagnosisPage>;
  enroll(principal: MonitoringPrincipal, input: unknown): Promise<never>;
}
