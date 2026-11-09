import express, { type RequestHandler } from "express";
import { withWorkflowBackgroundContext } from '@avernet/clawweb-shared/server/services/workflow-background-log';
import compression from "compression";
import cookieParser from "cookie-parser";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createClawevolveModule } from "@avernet/clawevolve";
import { createInsightRuntime, createInsightManagementRuntime, createInsightRouter, createSessionRecoveryRouter, type SessionRecoveryRuntime } from "@avernet/clawinsight";
import { createApprovalRouter } from "@avernet/workflow/server/routes/approval";
import { createInternalRunsRouter } from "@avernet/workflow/server/routes/internal/runs";
import { createInternalBotWorkflowPermissionsRouter } from "@avernet/workflow/server/routes/internal/bot-workflow-permissions";
import { createInternalApprovalCardsRouter } from "@avernet/workflow/server/routes/internal/approval-cards";
import { ApprovalCardRepository } from "@avernet/workflow/server/repositories/approval-card-repository";
import {
  createRunsRouter,
  createWorkflowsRouter,
} from "@avernet/workflow/server";
import { createAutoHealRouter } from "@avernet/workflow/server/routes/auto-heal";
import { createSubworkflowObservabilityRouter } from "@avernet/workflow/server/routes/subworkflow-observability";
import { createSubworkflowAuthorizer } from "@avernet/workflow/server/routes/subworkflow-authorization";
import { createPackVersionRouter } from "@avernet/workflow/server/routes/pack-version";
import { createWorkflowUnifiedSaveRouter, unifiedSaveHostGuard } from "@avernet/workflow/server/routes/workflow-unified-save";
import { WorkflowUnifiedSaveService, type WorkflowPackWriteAuthorization } from "@avernet/workflow/server/services/workflow-unified-save-service";
import type { AuthorizeSnapshotDeploy } from "@avernet/workflow/server/services/auto-heal-deploy-service";
import type { ClawFlowsGitConfig } from "@avernet/workflow/server/services/clawflows-git-client";
import { createEditorDraftCasRouter } from "@avernet/workflow/server/routes/editor-draft-cas";
import { EditorDraftCasService } from "@avernet/workflow/server/services/editor-draft-cas-service";
import { readActivePackVersionFromRemote, readPackOperationFromRemote, reconcilePackOperationFromRemote } from "@avernet/workflow/server/services/pack-version-remote";
import { createPackScriptsRouter } from "@avernet/workflow/server/routes/pack-scripts";
import { readPackScriptsFromRemote } from "@avernet/workflow/server/services/pack-script-read-service";
import { createWorkflowNodeStatsRouter } from "@avernet/workflow/server/routes/workflow-node-stats";
import { createWorkflowVersionComparisonService } from "@avernet/workflow/server/services/workflow-version-comparison-service";
import { createWorkflowVersionSourceService } from "@avernet/workflow/server/services/workflow-version-source-service";
import { WorkflowSpecRepository } from "@avernet/workflow/server/repositories/workflow-spec-repository";
import { WorkflowDeployHistoryRepository } from "@avernet/workflow/server/repositories/workflow-deploy-history-repository";
import { FlowRunRepository } from "@avernet/workflow/server/repositories/flow-run-repository";
import { NodeExecutionRepository } from "@avernet/workflow/server/repositories/node-execution-repository";
import { FlowEventRepository } from "@avernet/workflow/server/repositories/event-repository";
import { MetricsRepository } from "@avernet/workflow/server/repositories/metrics-repository";
import { AlertRepository } from "@avernet/workflow/server/repositories/alert-repository";
import { FlowControlRepository } from "@avernet/workflow/server/repositories/flow-control-repository";
import { ExecutionStepLogRepository } from "@avernet/workflow/server/repositories/execution-step-log-repository";
import { RunSubworkflowsRepository } from "@avernet/workflow/server/repositories/run-subworkflows-repository";
import { RunSubworkflowsService } from "@avernet/workflow/server/services/run-subworkflows-service";
import { FacadeBindingRepository } from "@avernet/workflow/server/repositories/facade-binding-repository";
import { AppConfigRepository } from "@avernet/workflow/server/repositories/app-config-repository";
import { createAutoAnalysisSettings } from "@avernet/workflow/server/services/task-guard/auto-analysis-settings";
import {
  closeDatabase,
  initDatabase,
  resolveAdminConfigFrom,
  resolveDynamicAdminConfig,
  resolveAutoHealConfig,
  resolveSignatureConfig,
  resolveFlowControlConfig,
} from "@avernet/clawweb-shared/server/db";
import { adminAuthMiddleware } from "@avernet/clawweb-shared/server/middleware/admin-auth";
import { AdminUserRepository } from "@avernet/clawweb-shared/server/repositories/admin-user-repository";
import { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { getClawWebPublicBaseUrl } from "@avernet/clawweb-shared/server/env";
import type { ClawWebBootstrap } from "./bootstrap.js";
import { closeHttpServer } from "./shutdown.js";
import { mountStaticFrontend } from "./static-frontend.js";
import { createAuthMeHandler, resolveAuthMeIdentity } from "./auth-me.js";
import { createWorkflowRepairRuntime } from "./repair-workbench-runtime.js";
import { AistudioService, resolveAistudioConfig } from "@avernet/clawinsight/server/services/aistudio-service";
import { createRepairAisExecution, resolveRepairAisConfiguration } from "./repair-ais-execution.js";
import { createInternalEngineAuth } from "./internal-engine-auth.js";
import { createAutoHealTriggerRun } from "./auto-heal-trigger-run.js";

/** Non-browser clients may omit Origin, but must still send the CSRF header. */
const packWriteGuard: RequestHandler = (req, res, next) => {
  const origin = req.get("origin");
  if (req.method === "GET" || req.method === "HEAD") {
    if ((origin && origin !== `${req.protocol}://${req.get("host")}`) || req.get("sec-fetch-site") === "cross-site") {
      res.status(403).json({ error: "Forbidden", code: "CSRF_REJECTED" }); return;
    }
    next(); return;
  }
  if (req.get("x-pack-version-csrf") !== "1" || (origin && origin !== `${req.protocol}://${req.get("host")}`)
    || req.get("sec-fetch-site") === "cross-site") {
    res.status(403).json({ error: "Forbidden", code: "CSRF_REJECTED" }); return;
  }
  next();
};

/** Explicitly trusted server composition. Do not derive identity, Git config or
 * Pack authorization from unverified headers/cookies or request body. The host
 * must coordinate every other writer and protect all shared/unlisted Pack paths.
 * Candidate bundles contain complete Git history and must never leave the host.
 * The host/ingress must enforce per-principal rate limits and bounded in-flight
 * authentication and saves, retaining slots until background work actually ends.
 */
export type UnifiedSaveHost = Readonly<{
  resolveVerifiedPrincipal(request: express.Request): Promise<{ actorId: string } | null>;
  resolveRepository(workflowId: string, actorId: string): Promise<{
    repositoryId: string; config: ClawFlowsGitConfig;
  } | null>;
  /** Optional additional restriction; workflow edit and DB-bound Pack checks always apply. */
  canWriteExactCandidate?(candidate: WorkflowPackWriteAuthorization): Promise<boolean>;
}>;

/** Server-only ACL for complete Editor deployment candidates; omission denies
 * Editor deployment. It never supplies identity or grants Pack file writes. */
export function createClawWebBootstrap(options: { sessionRecovery?: SessionRecoveryRuntime; unifiedSave?: UnifiedSaveHost; authorizeSnapshotDeploy?: AuthorizeSnapshotDeploy } = {}): ClawWebBootstrap {
  return {
    async start(context) {
      const port = Number.parseInt(process.env.PORT ?? "3001", 10);
      const aisConfig = resolveAistudioConfig();
      const repairConfig = await resolveRepairAisConfiguration(aisConfig.token,
        process.env.CLAWWEB_REPAIR_AIS_SNAPSHOT_ID, process.env.CLAWWEB_REPAIR_CLAWFLOWS_GIT_DIR);
      const { db } = await initDatabase();
      if (db.dbType === "noop") throw new Error("Public ClawWeb requires an available database");
      const insight = createInsightRuntime(db);
      const clawevolve = createClawevolveModule({ db });
      await clawevolve.start();
      const adminUserRepo = new AdminUserRepository(db);
      const fallbackAdminConfig = resolveAdminConfigFrom(context.config);
      // Seed the configured admin lists into the dynamic roster, then read the enabled one back:
      // runtime grants survive, and YAML stays authoritative for whatever it declares.
      let adminConfig;
      try {
        adminConfig = await resolveDynamicAdminConfig(db, fallbackAdminConfig);
      } catch (err) {
        console.warn(
          `[clawweb] Failed to seed dynamic admin config: ${err instanceof Error ? err.message : String(err)}`,
        );
        adminConfig = fallbackAdminConfig;
      }
      const app = express();
      app.use(compression());
      // This adapter consumes the raw body itself. Mount before *any* body parser;
      // parsing here first would defeat its duplicate-key and bounded-body checks.
      const configuredHost = options.unifiedSave;
      const candidatePolicy = configuredHost?.canWriteExactCandidate;
      const unifiedSaveHost = configuredHost
        && typeof configuredHost.resolveVerifiedPrincipal === 'function'
        && typeof configuredHost.resolveRepository === 'function'
        && (!('canWriteExactCandidate' in configuredHost) || typeof candidatePolicy === 'function')
        ? configuredHost : undefined;
      const unifiedPermissions = new BotWorkflowPermissionRepository(db);
      const unifiedDraft = new EditorDraftCasService(db, unifiedPermissions);
      app.use(/^\/api\/workflows\/[^/]+\/unified-save\/?$/i, unifiedSaveHostGuard(), packWriteGuard);
      app.use("/api/workflows", createWorkflowUnifiedSaveRouter({
        // Disabled means no external identity/ACL or repository lookup at all.
        resolvePrincipal: request => unifiedSaveHost ? unifiedSaveHost.resolveVerifiedPrincipal(request) : Promise.resolve(null),
        resolveAuthorizedService: async (workflowId, principal) => {
          if (!unifiedSaveHost) return null;
          const canEdit = (id: string, actor: string) => unifiedPermissions.hasEditPermission(id, actor);
          if (!await canEdit(workflowId, principal.actorId)) return null;
          const draft = await unifiedDraft.read(workflowId, principal.actorId);
          const repository = await unifiedSaveHost.resolveRepository(workflowId, principal.actorId);
          if (!repository) return null;
          const service = new WorkflowUnifiedSaveService({ draft: unifiedDraft, canEdit,
            ...(candidatePolicy ? { canWritePack: (candidate: WorkflowPackWriteAuthorization) => candidatePolicy.call(unifiedSaveHost, candidate) } : {}),
            repositoryId: repository.repositoryId, config: { ...repository.config } });
          return { packId: draft.packId ?? workflowId, repositoryId: repository.repositoryId, service };
        },
      }));
      app.use(express.json({ limit: "10mb" }));
      app.use(cookieParser());
      app.use(adminAuthMiddleware({ config: fallbackAdminConfig, repository: adminUserRepo }));
      app.get("/api/auth/me", createAuthMeHandler({ environment: context.environment }));
      app.get("/health", (_request, response) => {
        response.json({
          status: "ok",
          db: db.dbType,
          clawevolveImplementation: "avernet",
          profile: context.profile,
          environment: context.environment,
        });
      });
      app.use("/api/evolve", clawevolve.publicRouter);
      const repairExecution = repairConfig
        ? createRepairAisExecution(db, new AistudioService(aisConfig), repairConfig.gitStore, repairConfig.snapshotId, getClawWebPublicBaseUrl())
        : undefined;
      const botPermRepo = new BotWorkflowPermissionRepository(db);
      const deployHistoryRepo = new WorkflowDeployHistoryRepository(db);
      const workflowSpecRepo = new WorkflowSpecRepository(db, deployHistoryRepo);
      const publicBaseUrl = getClawWebPublicBaseUrl();
      console.info('[workflow-notification] ' + JSON.stringify({ event: 'provider_configured', environment: context.environment,
        senderConfigured: !!insight.notificationSender, publicBaseUrlConfigured: !!publicBaseUrl }));
      const repairRuntime = createWorkflowRepairRuntime(db, {
        environment: context.environment,
        principal: async request => {
          const identity = resolveAuthMeIdentity(request, { environment: context.environment });
          if (!identity) return null;
          return { actorId: identity.userId, isAdmin: identity.userId === "dev_local"
            || await adminUserRepo.hasRole([identity.userId, identity.userName], "admin") };
        },
        execution: repairExecution,
        notifications: {
          settings: createAutoAnalysisSettings({ repo: new AppConfigRepository(db) }),
          ports: {
            owner: async workflowId => {
              const row = await workflowSpecRepo.findSummaryByWorkflowId(workflowId);
              return row?.resolved_owner_id?.trim() || row?.owner_id?.trim() || null;
            },
            canEdit: (workflowId, actorId) => botPermRepo.hasEditPermission(workflowId, actorId),
            async notify(notice) {
              if (!insight.notificationSender || !publicBaseUrl) {
                console.warn('[workflow-notification] ' + JSON.stringify({ event: 'provider_unavailable', noticeId: notice.key,
                  workflowId: notice.workflowId, reason: !insight.notificationSender ? 'NOTIFICATION_SENDER_MISSING' : 'NOTIFICATION_PUBLIC_URL_MISSING' }));
                return { status: 'retryable' };
              }
              try {
                const link = new URL(notice.path, publicBaseUrl).toString();
                const sent = await insight.notificationSender.sendNotification({ recipientUserId: notice.recipientId,
                  title: notice.title, text: `${notice.text}\n\n[查看工作流并处理](${link})` });
                return { status: 'sent', ...(sent.processQueryKey ? { receipt: sent.processQueryKey } : {}) };
              } catch (error) {
                const message = error instanceof Error ? error.message : '';
                const httpStatus = message.match(/HTTP (400|401|403|404|422|429)\b/)?.[1];
                const missingConfiguration = message.includes('not configured');
                console.warn('[workflow-notification] ' + JSON.stringify({ event: 'provider_failed', noticeId: notice.key,
                  workflowId: notice.workflowId, httpStatus: httpStatus ?? null,
                  reason: missingConfiguration ? 'NOTIFICATION_PROVIDER_NOT_CONFIGURED' : httpStatus ? 'NOTIFICATION_HTTP_REJECTED' : 'NOTIFICATION_TRANSPORT_UNKNOWN' }));
                // Preserve unknown transport outcomes; the legacy sender has no idempotency key.
                return { status: missingConfiguration || httpStatus ? 'retryable' : 'uncertain' };
              }
            },
          },
        },
      });
      app.use("/api/workflow-repairs", repairRuntime.router);
      let repairPollBusy = false;
      const repairPoll = setInterval(() => {
        if (repairPollBusy) return;
        repairPollBusy = true;
        void withWorkflowBackgroundContext({ trigger: 'timer', environment: context.environment }, () => repairRuntime.monitor.reconcile()).catch(() => {
          console.warn('[clawweb] Workflow Repair reconciliation failed; will retry');
        }).finally(() => { repairPollBusy = false; });
      }, 15_000);
      repairPoll?.unref();
      app.use("/api/bench", clawevolve.benchRouter);
      app.use("/api/insight/v1", createSessionRecoveryRouter(options.sessionRecovery));
      app.use("/api/insight/v1", createInsightManagementRuntime(db).router);
      app.use("/api/insight/v1", createInsightRouter(insight.service));
      // Approval API: user-facing (GET/POST /api/approval/:id/...) and internal (POST /api/approval-cards/...)
      const approvalCardRepo = new ApprovalCardRepository(db);
      app.use("/api/approval", createApprovalRouter(db, {
        resolveSessionIdentity: async (request) => {
          const identity = resolveAuthMeIdentity(request, { environment: context.environment });
          return identity
            ? { ok: true, userId: identity.userId }
            : { ok: false, error: "未登录或登录状态已失效" };
        },
      }));
      app.use("/api/approval-cards", createInternalApprovalCardsRouter(approvalCardRepo));

      // AutoHeal fallbacks only; the router resolves DB overrides on each save/deploy.
      const autoHealConfig = resolveAutoHealConfig();
      const autoHealGitConfig = {
        remoteUrl: autoHealConfig.clawflowsRemoteUrl,
        username: autoHealConfig.gitUsername,
        email: autoHealConfig.gitAuthorEmail,
        token: autoHealConfig.gitToken,
      };
      const autoHealAuthor = {
        name: autoHealConfig.gitAuthorName,
        email: autoHealConfig.gitAuthorEmail,
      };
      const flowRunRepo = new FlowRunRepository(db);
      const nodeExecRepo = new NodeExecutionRepository(db);
      const eventRepo = new FlowEventRepository(db);
      const metricsRepo = new MetricsRepository(db);
      const alertRepo = new AlertRepository(db);
      const flowControlRepo = new FlowControlRepository(db);
      const execStepLogRepo = new ExecutionStepLogRepository(db);
      const engineAuth = createInternalEngineAuth({
        environment: context.environment,
        publicKeyB64: resolveSignatureConfig().publicKeyB64,
      });
      app.use("/api/internal/runs", engineAuth, createInternalRunsRouter(
        flowRunRepo, nodeExecRepo, resolveFlowControlConfig(),
      ));
      app.use("/api/internal/bot-workflow-permissions", engineAuth, createInternalBotWorkflowPermissionsRouter(botPermRepo));
      const facadeRepo = new FacadeBindingRepository(db);
      // The public development identity can come from an unverified cookie.
      // Do not expose scripts until a verified principal and Pack ACL exist.
      app.use("/api/workflows", createPackScriptsRouter({
        enabled: false,
        resolvePrincipal: async () => null,
        botPermRepo,
        workflowSpecRepo,
        supportsVersionedSnapshots: true,
        supportsVersionedPackResolution: true,
        readSnapshot: (workflowId, packId, version) => readPackScriptsFromRemote({
          db, gitConfig: autoHealGitConfig, gitAuthor: autoHealAuthor, workflowId, packId, version,
        }),
      }));
      const resolvePackPrincipal = async (request: express.Request) => {
        const identity = resolveAuthMeIdentity(request, { environment: context.environment });
        // Local cookies may select arbitrary names, so only the fixed local developer
        // identity is eligible for these sensitive public-host operations.
        return identity?.userId === "dev_local" ? { actorId: "dev_local" } : null;
      };
      const packContext = { db, gitConfig: autoHealGitConfig, gitAuthor: autoHealAuthor };
      // Snapshot reads require the same verified local principal and edit ACL as
      // journal recovery. File writes remain unavailable on the public host.
      app.use(/^\/api\/workflows\/[^/]+\/(?:editor-draft|pack-version|pack-operations\/[^/]+(?:\/reconcile)?)\/?$/i, packWriteGuard);
      const editorDraftCas = new EditorDraftCasService(db, botPermRepo);
      app.use("/api/workflows", createEditorDraftCasRouter({
        resolvePrincipal: resolvePackPrincipal,
        read: (workflowId, actorId) => editorDraftCas.read(workflowId, actorId),
        save: input => editorDraftCas.save(input),
      }));
      app.use("/api/workflows", createPackVersionRouter({
        resolvePrincipal: resolvePackPrincipal,
        botPermRepo, workflowSpecRepo,
        readActiveSnapshot: (workflowId, version) => readActivePackVersionFromRemote({ ...packContext, workflowId, version }),
        readSnapshot: (workflowId, packId, version) => readPackScriptsFromRemote({
          ...packContext, workflowId, packId, version, includeVersionFiles: true,
        }),
        readOperation: (requestId, workflowId, actorId) => readPackOperationFromRemote({ ...packContext, requestId, workflowId, actorId }),
        reconcileOperation: (requestId, workflowId, actorId) => reconcilePackOperationFromRemote({ ...packContext, requestId, workflowId, actorId }),
      }));
      app.use("/api/workflows", createWorkflowsRouter(
        workflowSpecRepo,
        null,
        botPermRepo,
        null,
        deployHistoryRepo,
        null,
        createWorkflowVersionSourceService(db),
      ));
      app.use("/api/workflows", createWorkflowNodeStatsRouter(
        null, null, createWorkflowVersionComparisonService(db), botPermRepo,
      ));
      // The public host only has a verified local-development identity. Production
      // hosts must inject their own verified principal instead of trusting headers.
      const authorizeSubworkflows = createSubworkflowAuthorizer(async request => {
        const identity = resolveAuthMeIdentity(request, { environment: context.environment });
        if (!identity) return null;
        return { actorId: identity.userId, isAdmin: identity.userId === "dev_local"
          || await adminUserRepo.hasRole([identity.userId, identity.userName], "admin") };
      }, botPermRepo);
      app.use("/api/runs", createSubworkflowObservabilityRouter(db, authorizeSubworkflows));
      app.use("/api/runs", createRunsRouter(
        flowRunRepo,
        nodeExecRepo,
        eventRepo,
        metricsRepo,
        alertRepo,
        flowControlRepo,
        botPermRepo,
        workflowSpecRepo,
        execStepLogRepo,
        facadeRepo,
        new RunSubworkflowsService(new RunSubworkflowsRepository(db), botPermRepo),
      ));
      app.use(["/api/auto-heal/save", "/api/auto-heal/unified-save", "/api/auto-heal/editor-save-first-draft", "/api/auto-heal/deploy", "/api/auto-heal/editor-deploy"], packWriteGuard);
      app.use("/api/auto-heal", createAutoHealRouter({
        resolvePrincipal: resolvePackPrincipal,
        authorizeSnapshotDeploy: options.authorizeSnapshotDeploy,
        db,
        flowRunRepo,
        workflowSpecRepo,
        deployHistoryRepo,
        botPermRepo,
        gitConfig: autoHealGitConfig,
        gitAuthor: autoHealAuthor,
        triggerRun: createAutoHealTriggerRun(flowRunRepo),
        facadeRepo,
      }));

      const staticDir = resolve(dirname(fileURLToPath(import.meta.url)), "../web");
      mountStaticFrontend(app, staticDir);
      const server = app.listen(port, "127.0.0.1", () => {
        console.info(`[clawweb] Public runner listening on http://127.0.0.1:${port}`);
      });
      return {
        stop: async () => {
          if (repairPoll) clearInterval(repairPoll);
          await closeHttpServer(server);
          await clawevolve.stop();
          await closeDatabase();
        },
      };
    },
  };
}
