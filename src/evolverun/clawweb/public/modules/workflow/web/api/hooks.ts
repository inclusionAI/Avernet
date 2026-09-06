import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@avernet/clawweb-shared/web/api/client'
import type { BenchDomain } from '@avernet/clawweb-shared/web/types'
import type { WorkflowSpec, KnowledgeBaseCreateInput, KnowledgeBaseUpdateInput, ValidationTemplateCreateInput, ValidationTemplateUpdateInput, ThetaTraceQueryParams, InterveneRequest, InterveneResult, SessionInfoUpdate, NodeStepTraceData, HallucinationCheckData, AutoHealDiagnosisResult, SmartOnboardingGenerateRequest, SmartOnboardingGenerationStatus, SmartOnboardingTestRunRequest, SmartOnboardingValidateRequest, SmartOnboardingValidateResult, TCLogQueryParams, TCLogTaskListParams, TCLogTaskSearchParams, YuQueBookInfo, AppConfigCreateInput, AppConfigUpdateInput, AdminUsersListResponse, AdminUserCreateInput } from '@avernet/clawweb-shared/web/types'

export function useFlowRuns(params?: {
  status?: string
  workflowId?: string
  limit?: number
  offset?: number
  from?: string
  to?: string
  botOwnerId?: string
  botId?: string
  inputQuery?: string
  enabled?: boolean
}) {
  const { enabled, ...queryParams } = params ?? {}
  return useQuery({
    queryKey: ['runs', queryParams],
    queryFn: () => api.runs.list(queryParams),
    enabled: enabled !== false,
  })
}

export function useWorkflowTypes(botOwnerId?: string, botId?: string, status?: string) {
  return useQuery({
    queryKey: ['workflow-types', botOwnerId, botId, status],
    queryFn: () => api.runs.workflowTypes(botOwnerId, botId, 500, undefined, status),
    select: (data) => data.workflows,
  })
}

export function useEvolveLessons(params: { workflowId?: string; status?: string; query?: string; limit?: number; offset?: number; enabled?: boolean } = {}) {
  const { enabled, ...queryParams } = params
  return useQuery({
    queryKey: ['evolve-lessons', queryParams],
    queryFn: () => api.evolve.listLessons(queryParams),
    enabled: enabled !== false,
  })
}

export function useEvolveDiagnoses(params: { workflowId?: string; flowId?: string; analysisId?: string; query?: string; limit?: number; offset?: number; enabled?: boolean } = {}) {
  const { enabled, ...queryParams } = params
  return useQuery({
    queryKey: ['evolve-diagnoses', queryParams],
    queryFn: () => api.evolve.listDiagnoses(queryParams),
    enabled: enabled !== false,
  })
}


export function useEvolveSuggestions(params: { workflowId: string; status?: string; limit?: number; offset?: number; enabled?: boolean }) {
  return useQuery({
    queryKey: ['evolve-suggestions', params.workflowId, params.status, params.limit, params.offset],
    queryFn: () => api.evolve.listSuggestions({ workflowId: params.workflowId, status: params.status, limit: params.limit, offset: params.offset }),
    enabled: params.enabled !== false && !!params.workflowId,
  })
}
export function useCreateLesson() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.evolve.createLesson,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['evolve-lessons'] })
    },
  })
}

export function useUpdateLesson() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ lessonId, input }: { lessonId: string; input: Parameters<typeof api.evolve.updateLesson>[1] }) => api.evolve.updateLesson(lessonId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['evolve-lessons'] })
    },
  })
}

export function usePromoteDiagnosisToLesson() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ diagnosisId, input }: { diagnosisId: string; input?: Parameters<typeof api.evolve.promoteDiagnosisToLesson>[1] }) =>
      api.evolve.promoteDiagnosisToLesson(diagnosisId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['evolve-lessons'] })
      void queryClient.invalidateQueries({ queryKey: ['evolve-suggestions'] })
    },
  })
}


export function useEligibleBotsForSuggestion(suggestionId?: string, enabled?: boolean) {
  return useQuery({
    queryKey: ['evolve-suggestion-eligible-bots', suggestionId],
    queryFn: () => api.evolve.listEligibleBotsForSuggestion({ suggestionId: suggestionId! }),
    enabled: (enabled !== false) && !!suggestionId,
  })
}

export function useApplySuggestion() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: Parameters<typeof api.evolve.applySuggestion>[0]) => api.evolve.applySuggestion(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['evolve-suggestions'] })
      void queryClient.invalidateQueries({ queryKey: ['evolve-suggestion-eligible-bots'] })
    },
  })
}
export function useApplySuggestionsBatch() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: Parameters<typeof api.evolve.applySuggestionsBatch>[0]) => api.evolve.applySuggestionsBatch(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['evolve-suggestions'] })
      void queryClient.invalidateQueries({ queryKey: ['evolve-suggestion-eligible-bots'] })
      void queryClient.invalidateQueries({ queryKey: ['evolve-suggestion-apply-tasks'] })
    },
  })
}
export function useRecordSuggestionAction() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: Parameters<typeof api.evolve.recordSuggestionAction>[0]) => api.evolve.recordSuggestionAction(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['evolve-suggestions'] })
    },
  })
}

export function useSuggestionApplyTasks(
  suggestionIds: string[],
  options?: { enabled?: boolean; refetchInterval?: number | false; refetchIntervalInBackground?: boolean; staleTime?: number }
) {
  return useQuery({
    queryKey: ['evolve-suggestion-apply-tasks', suggestionIds],
    queryFn: () => api.evolve.getSuggestionApplyTasks(suggestionIds),
    enabled: (options?.enabled !== false) && suggestionIds.length > 0,
    refetchInterval: options?.refetchInterval ?? 3000,
    refetchIntervalInBackground: options?.refetchIntervalInBackground ?? false,
    staleTime: options?.staleTime ?? 0,
  })
}

export function useAnalyzeRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { flowId: string; botId?: string; botEnv?: string }) => api.evolve.analyzeRun(input.flowId, input.botId, input.botEnv),
    onSuccess: (_data, input) => {
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
      void queryClient.invalidateQueries({ queryKey: ['evolve-diagnoses'] })
      void queryClient.invalidateQueries({ queryKey: ['evolve-suggestions'] })
      void queryClient.invalidateQueries({ queryKey: ['run', input.flowId] })
    },
  })
}

export function useAnalysisProgress(flowId: string, enabled = true) {
  return useQuery({
    queryKey: ['workflow-analysis-progress', flowId],
    queryFn: () => api.evolve.getAnalysisProgress(flowId),
    enabled: enabled && Boolean(flowId),
    refetchInterval: enabled ? 3000 : false,
    refetchIntervalInBackground: false,
    staleTime: 0,
  })
}

export function useRunEvolutionAnalysis(flowId: string, analysisId?: string, enabled = true) {
  return useQuery({
    queryKey: ['run-evolution-analysis', flowId, analysisId],
    queryFn: () => api.evolve.getRunAnalysisResult(flowId, analysisId),
    enabled: enabled && Boolean(flowId),
  })
}

export function useEligibleBotsForAnalyze(workflowId: string | undefined) {
  return useQuery({
    queryKey: ['evolve-analyze-eligible-bots', workflowId],
    queryFn: () => api.evolve.listEligibleBotsForAnalyze(workflowId!),
    enabled: !!workflowId,
  })
}

export function useResetAnalysisRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (flowId: string) => api.evolve.resetAnalysisRun(flowId),
    onSuccess: (_data, flowId) => {
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
      void queryClient.invalidateQueries({ queryKey: ['run', flowId] })
    },
  })
}


export function useAnalyzeWorkflowLogs() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: Parameters<typeof api.evolve.analyzeWorkflowLogs>[0]) => api.evolve.analyzeWorkflowLogs(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['evolve-diagnoses'] })
      void queryClient.invalidateQueries({ queryKey: ['evolve-suggestions'] })
    },
  })
}

export function useRunTimeline(flowId: string | undefined) {
  return useQuery({
    queryKey: ['run-timeline', flowId],
    queryFn: () => api.runArchives.getTimeline(flowId!),
    enabled: !!flowId,
  })
}

export function useAnalyzeFlow() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (params: { flowId: string; workflowId: string }) => api.evolve.analyzeFlow(params),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
      void queryClient.invalidateQueries({ queryKey: ['evolve-diagnoses'] })
    },
  })
}

export function useDeleteFlowRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (flowId: string) => api.runs.delete(flowId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
      void queryClient.invalidateQueries({ queryKey: ['workflow-types'] })
    },
  })
}

export function useRerunFlowRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (flowId: string) => api.runs.rerun(flowId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
      void queryClient.invalidateQueries({ queryKey: ['workflow-types'] })
    },
  })
}

export function useFlowRun(flowId: string) {
  return useQuery({
    queryKey: ['run', flowId],
    queryFn: () => api.runs.get(flowId),
    enabled: !!flowId,
  })
}

export function useNodeExecutions(flowId: string, full = false) {
  return useQuery({
    queryKey: ['nodes', flowId, full],
    queryFn: () => api.runs.nodes(flowId, full),
    enabled: !!flowId,
  })
}

export function useFlowEvents(flowId: string) {
  return useQuery({
    queryKey: ['events', flowId],
    queryFn: () => api.runs.events(flowId),
    enabled: !!flowId,
  })
}

export function useDbWorkflow(workflowId: string) {
  return useQuery({
    queryKey: ['db-workflow', workflowId],
    queryFn: () => api.workflows.get(workflowId),
    enabled: !!workflowId,
  })
}

export function useSaveWorkflowToDb() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ workflowId, spec, packId, facade, originalWorkflowId, botOwnerId, botId }: {
      workflowId: string
      spec: WorkflowSpec
      packId?: string
      facade?: { command?: string; remark?: string }
      originalWorkflowId?: string
      botOwnerId?: string
      botId?: string
    }) =>
      api.workflows.save(workflowId, spec, { packId, facade, originalWorkflowId, botOwnerId, botId }),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['db-workflows'] })
      void queryClient.invalidateQueries({ queryKey: ['facade-bindings'] })
      void queryClient.invalidateQueries({ queryKey: ['db-workflow', variables.workflowId] })
      if (variables.originalWorkflowId && variables.originalWorkflowId !== variables.workflowId) {
        void queryClient.invalidateQueries({ queryKey: ['db-workflow', variables.originalWorkflowId] })
      }
    },
  })
}

export function useCreateWorkflow() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ workflowId, spec, facade, botOwnerId, botId }: {
      workflowId: string
      spec: WorkflowSpec
      facade?: { command?: string; remark?: string }
      botOwnerId?: string
      botId?: string
    }) =>
      api.workflows.save(workflowId, spec, {
        facade,
        botOwnerId,
        botId,
      }),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['workflow-types'] })
      void queryClient.invalidateQueries({ queryKey: ['db-workflows'] })
      void queryClient.invalidateQueries({ queryKey: ['facade-bindings'] })
      void queryClient.invalidateQueries({ queryKey: ['db-workflow', variables.workflowId] })
    },
  })
}

export function useDbWorkflows(botOwnerId?: string, botId?: string) {
  return useQuery({
    queryKey: ['db-workflows', botOwnerId, botId],
    queryFn: () => api.workflows.list(botOwnerId, botId),
  })
}

export function useDryRun() {
  return useMutation({
    mutationFn: api.dryRun,
  })
}

export function useKnowledgeBases(enabledOnly = false) {
  return useQuery({
    queryKey: ['knowledge-bases', enabledOnly],
    queryFn: () => api.knowledgeBases.list(enabledOnly),
  })
}

export function useKnowledgeBase(kbId: string) {
  return useQuery({
    queryKey: ['knowledge-base', kbId],
    queryFn: () => api.knowledgeBases.get(kbId),
    enabled: !!kbId,
  })
}

export function useCreateKnowledgeBase() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: KnowledgeBaseCreateInput) => api.knowledgeBases.create(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['knowledge-bases'] })
    },
  })
}

export function useUpdateKnowledgeBase() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ kbId, input }: { kbId: string; input: KnowledgeBaseUpdateInput }) =>
      api.knowledgeBases.update(kbId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['knowledge-bases'] })
    },
  })
}

export function useDeleteKnowledgeBase() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (kbId: string) => api.knowledgeBases.delete(kbId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['knowledge-bases'] })
    },
  })
}

export function useTestKnowledgeBase() {
  return useMutation({
    mutationFn: ({ kbId, query }: { kbId: string; query: string }) =>
      api.knowledgeBases.test(kbId, query),
  })
}

export function useYuQueSearch() {
  return useMutation({
    mutationFn: ({ query, bookId, bookIds }: { query: string; bookId?: number; bookIds?: number[] }) =>
      api.knowledgeBases.yuqueSearch(query, bookId, bookIds),
  })
}

export function useYuQueBooks() {
  return useQuery<YuQueBookInfo[]>({
    queryKey: ['yuque-books'],
    queryFn: () => api.knowledgeBases.yuqueBooks(),
  })
}

export function useSystemLogSearch() {
  return useMutation({
    mutationFn: (params: { keyword: string; sources?: string[]; from: number; to: number; limit?: number }) =>
      api.systemLogs.search(params),
  })
}

// --- Validation Template Hooks ---

export function useValidationTemplates(enabledOnly = false) {
  return useQuery({
    queryKey: ['validation-templates', enabledOnly],
    queryFn: () => api.validationTemplates.list(enabledOnly),
  })
}

export function useValidationTemplate(templateId: string) {
  return useQuery({
    queryKey: ['validation-template', templateId],
    queryFn: () => api.validationTemplates.get(templateId),
    enabled: !!templateId,
  })
}

export function useCreateValidationTemplate() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: ValidationTemplateCreateInput) => api.validationTemplates.create(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['validation-templates'] })
    },
  })
}

export function useUpdateValidationTemplate() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ templateId, input }: { templateId: string; input: ValidationTemplateUpdateInput }) =>
      api.validationTemplates.update(templateId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['validation-templates'] })
    },
  })
}

export function useDeleteValidationTemplate() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (templateId: string) => api.validationTemplates.delete(templateId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['validation-templates'] })
    },
  })
}

export function useTestValidationTemplate() {
  return useMutation({
    mutationFn: ({ templateId, sampleOutput }: { templateId: string; sampleOutput: string }) =>
      api.validationTemplates.test(templateId, sampleOutput),
  })
}

// --- Facade Binding Hooks ---

export function useFacadeBindings() {
  return useQuery({
    queryKey: ['facade-bindings'],
    queryFn: () => api.facades.list(),
  })
}

export function useDeleteFacadeBinding() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (command: string) => api.facades.delete(command),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['facade-bindings'] })
      void queryClient.invalidateQueries({ queryKey: ['facades-page'] })
    },
  })
}

// --- Paginated Workflow & Facade Hooks (for editor sidebar) ---

export function useWorkflowsPage(params: { page: number; pageSize: number; search?: string; enabled?: boolean }) {
  return useQuery({
    queryKey: ['workflows-page', params.page, params.pageSize, params.search],
    queryFn: () => api.workflows.listPage({ page: params.page, pageSize: params.pageSize, search: params.search }),
    enabled: params.enabled !== false,
  })
}

export function useFacadesPage(params: { page: number; pageSize: number; search?: string; enabled?: boolean }) {
  return useQuery({
    queryKey: ['facades-page', params.page, params.pageSize, params.search],
    queryFn: () => api.facades.listPage({ page: params.page, pageSize: params.pageSize, search: params.search }),
    enabled: params.enabled !== false,
  })
}

// --- Langfuse & Analysis Hooks ---

export function useLangfuseTraces(params: { sessionId: string; from?: string; to?: string }) {
  return useQuery({
    queryKey: ['langfuse-traces', params],
    queryFn: () => api.langfuse.traces(params),
    enabled: !!params.sessionId,
  })
}

export function useAnalyzeTrace() {
  return useMutation({
    mutationFn: (params: { traceData: unknown; nodeTitle: string; nodeId: string; nodeInput?: string; nodeOutput?: string; nodeError?: string }) =>
      api.analysis.analyze(params),
  })
}

// --- TCLog Hooks ---

export function useSandboxQuery() {
  return useMutation({
    mutationFn: (params: { botId: string; entityId: number }) =>
      api.sandboxQuery.query(params.botId, params.entityId),
  })
}

export function useTCLogBots(params?: { ownerId?: string; status?: 'active' | 'all' }, enabled = true) {
  return useQuery({
    queryKey: ['tclog-bots', params],
    queryFn: () => api.tclog.bots(params),
    enabled,
  })
}

export function useTCLogQuery(params: TCLogQueryParams, enabled = true) {
  return useQuery({
    queryKey: ['tclog-query', params],
    queryFn: () => api.tclog.query(params),
    enabled,
  })
}

export function useTCLogTasks(params: TCLogTaskListParams, enabled = true) {
  return useQuery({
    queryKey: ['tclog-tasks', params],
    queryFn: () => api.tclog.tasks(params),
    enabled,
  })
}

export function useTCLogTaskSearch(params: TCLogTaskSearchParams, enabled = true) {
  return useQuery({
    queryKey: ['tclog-task-search', params],
    queryFn: () => api.tclog.taskSearch(params),
    enabled,
  })
}

export function useTCLogTrace(traceId: string | null, ownerId?: string, dataSource?: 'auto' | 'tc' | 'langfuse', botId?: string, embed?: boolean) {
  return useQuery({
    queryKey: ['tclog-trace', traceId, ownerId, dataSource, botId, embed],
    queryFn: () => api.tclog.trace(traceId!, ownerId, dataSource, botId, embed),
    enabled: !!traceId,
  })
}

// --- Bench Domain Hooks ---

export function useBenchDomains(params: { admin?: boolean; ownerUserId?: string } = {}) {
  return useQuery({
    queryKey: ['bench-domains', params],
    queryFn: async (): Promise<BenchDomain[]> => {
      if (!params.admin) return api.bench.domains()
      const result = await api.bench.admin.domains({ ownerUserId: params.ownerUserId || undefined })
      return result.domains.map((domain) => ({
        id: 0,
        domainId: domain.domainId,
        name: domain.name,
        description: null,
        status: domain.status === 'archived' ? 'archived' : 'active',
        templateCount: domain.templateCount,
        createdBy: domain.ownerUserId,
        ownerUserId: domain.ownerUserId,
        gmtCreate: 0,
        gmtModified: 0,
      }))
    },
  })
}

export function useCreateBenchDomain() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: Parameters<typeof api.bench.createDomain>[0]) => api.bench.createDomain(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['bench-domains'] })
    },
  })
}

export function useUpdateBenchDomain() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ ownerUserId, domainId, input }: { ownerUserId: string; domainId: string; input: Parameters<typeof api.bench.updateDomain>[2] }) =>
      api.bench.updateDomain(ownerUserId, domainId, input),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['bench-domains'] })
      void queryClient.invalidateQueries({ queryKey: ['bench-domain', variables.ownerUserId, variables.domainId] })
    },
  })
}

export function useArchiveBenchDomain() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ ownerUserId, domainId }: { ownerUserId: string; domainId: string }) =>
      api.bench.archiveDomain(ownerUserId, domainId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['bench-domains'] })
      void queryClient.invalidateQueries({ queryKey: ['bench-templates'] })
      void queryClient.invalidateQueries({ queryKey: ['bench-runs'] })
    },
  })
}

export function useBenchDomain(ownerUserId: string, domainId: string) {
  return useQuery({
    queryKey: ['bench-domain', ownerUserId, domainId],
    queryFn: () => api.bench.domain(ownerUserId, domainId),
    enabled: !!ownerUserId && !!domainId,
  })
}

// --- Bench Template Hooks ---

export function useBenchTemplates(domainId?: string, params?: { ownerUserId?: string; status?: string }) {
  return useQuery({
    queryKey: ['bench-templates', domainId ?? '__all__', params],
    queryFn: () => api.bench.templates(domainId, params),
    enabled: domainId === undefined || domainId === '' || !!params?.ownerUserId,
  })
}

export function useBenchTemplate(ownerUserId: string, domainId: string, templateName: string) {
  return useQuery({
    queryKey: ['bench-template', ownerUserId, domainId, templateName],
    queryFn: () => api.bench.template(ownerUserId, domainId, templateName),
    enabled: !!ownerUserId && !!domainId && !!templateName,
  })
}

export function useCreateBenchTemplate(ownerUserId: string, domainId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: Parameters<typeof api.bench.createTemplate>[2]) =>
      api.bench.createTemplate(ownerUserId, domainId, input as Parameters<typeof api.bench.createTemplate>[2]),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['bench-templates'] })
      void queryClient.invalidateQueries({ queryKey: ['bench-templates', domainId] })
      void queryClient.invalidateQueries({ queryKey: ['bench-domain-summary', ownerUserId, domainId] })
      void queryClient.invalidateQueries({ queryKey: ['bench-domains'] })
    },
  })
}

export function useUpdateBenchTemplate(ownerUserId = '', domainId = '', templateName = '') {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: Parameters<typeof api.bench.updateTemplate>[3]) =>
      api.bench.updateTemplate(ownerUserId, domainId, templateName, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['bench-templates'] })
      void queryClient.invalidateQueries({ queryKey: ['bench-templates', domainId] })
      void queryClient.invalidateQueries({ queryKey: ['bench-template', ownerUserId, domainId, templateName] })
      void queryClient.invalidateQueries({ queryKey: ['bench-domain-summary', ownerUserId, domainId] })
    },
  })
}

export function usePublishBenchTemplate() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ ownerUserId, domainId, templateName, version }: { ownerUserId: string; domainId: string; templateName: string; version?: number }) =>
      api.bench.publishTemplate(ownerUserId, domainId, templateName, version),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['bench-templates'] })
      void queryClient.invalidateQueries({ queryKey: ['bench-templates', variables.domainId] })
      void queryClient.invalidateQueries({ queryKey: ['bench-template', variables.ownerUserId, variables.domainId, variables.templateName] })
      void queryClient.invalidateQueries({ queryKey: ['bench-domain-summary', variables.ownerUserId, variables.domainId] })
    },
  })
}

export function useBatchPublishBenchTemplates() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ ownerUserId, domainId, templates }: { ownerUserId: string; domainId: string; templates: Array<{ templateName: string; version?: number | null }> }) =>
      api.bench.batchPublishTemplates(ownerUserId, domainId, { templates }),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['bench-templates'] })
      void queryClient.invalidateQueries({ queryKey: ['bench-templates', variables.domainId] })
      void queryClient.invalidateQueries({ queryKey: ['bench-domain-summary', variables.ownerUserId, variables.domainId] })
    },
  })
}

export function useArchiveBenchTemplate() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ ownerUserId, domainId, templateName }: { ownerUserId: string; domainId: string; templateName: string }) =>
      api.bench.archiveTemplate(ownerUserId, domainId, templateName),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['bench-templates'] })
      void queryClient.invalidateQueries({ queryKey: ['bench-templates', variables.domainId] })
      void queryClient.invalidateQueries({ queryKey: ['bench-template', variables.ownerUserId, variables.domainId, variables.templateName] })
      void queryClient.invalidateQueries({ queryKey: ['bench-domain-summary', variables.ownerUserId, variables.domainId] })
      void queryClient.invalidateQueries({ queryKey: ['bench-domains'] })
    },
  })
}

export function useScanBenchTemplateUpload(ownerUserId: string, domainId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (files: File[]) => api.bench.uploadsScan(ownerUserId, domainId, files),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['bench-templates', domainId] })
      void queryClient.invalidateQueries({ queryKey: ['bench-domain-summary', ownerUserId, domainId] })
      void queryClient.invalidateQueries({ queryKey: ['bench-domains'] })
    },
  })
}

// --- Bench Run Hooks ---

export function useBenchRun(benchRunId: string) {
  return useQuery({
    queryKey: ['bench-run', benchRunId],
    queryFn: () => api.bench.run(benchRunId),
    enabled: !!benchRunId,
  })
}

export function useBenchRunsByTemplate(ownerUserId: string, domainId: string, templateName: string) {
  return useQuery({
    queryKey: ['bench-runs-by-template', ownerUserId, domainId, templateName],
    queryFn: () => api.bench.runsByTemplate(ownerUserId, domainId, templateName),
    enabled: !!ownerUserId && !!domainId && !!templateName,
  })
}

export function useBenchRuns(params?: Parameters<typeof api.bench.runs>[0] & { enabled?: boolean }) {
  const { enabled, ...queryParams } = params ?? {}
  return useQuery({
    queryKey: ['bench-runs', queryParams],
    queryFn: () => api.bench.runs(queryParams),
    enabled: enabled !== false,
  })
}

export function useAdminBenchRuns(params?: Parameters<typeof api.bench.adminRuns>[0] & { enabled?: boolean }) {
  const { enabled, ...queryParams } = params ?? {}
  return useQuery({
    queryKey: ['bench-admin-runs', queryParams],
    queryFn: () => api.bench.adminRuns(queryParams),
    enabled: enabled !== false,
  })
}

export function useBenchDomainSummary(ownerUserId: string, domainId: string) {
  return useQuery({
    queryKey: ['bench-domain-summary', ownerUserId, domainId],
    queryFn: () => api.bench.domainSummary(ownerUserId, domainId),
    enabled: !!ownerUserId && !!domainId,
  })
}

export function useCreateBenchRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: Parameters<typeof api.bench.createRun>[0]) => api.bench.createRun(input),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['bench-runs-by-template', variables.domainId, variables.templateName] })
    },
  })
}

export function useUpdateBenchRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ benchRunId, input }: { benchRunId: string; input: Parameters<typeof api.bench.updateRun>[1] }) =>
      api.bench.updateRun(benchRunId, input),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['bench-run', variables.benchRunId] })
    },
  })
}

export function useBenchResults(benchRunId: string) {
  return useQuery({
    queryKey: ['bench-results', benchRunId],
    queryFn: () => api.bench.results(benchRunId),
    enabled: !!benchRunId,
  })
}

export function useBenchArtifacts(benchRunId: string, params?: { artifactType?: string; taskId?: string; includeContent?: boolean }) {
  return useQuery({
    queryKey: ['bench-artifacts', benchRunId, params],
    queryFn: () => api.bench.artifacts(benchRunId, params),
    enabled: !!benchRunId,
  })
}

export function useBenchSessions(benchRunId: string) {
  return useQuery({
    queryKey: ['bench-sessions', benchRunId],
    queryFn: () => api.bench.sessions(benchRunId),
    enabled: !!benchRunId,
  })
}

export function useBenchSession(benchRunId: string, taskId: string | null) {
  return useQuery({
    queryKey: ['bench-session', benchRunId, taskId],
    queryFn: () => api.bench.session(benchRunId, taskId ?? ''),
    enabled: !!benchRunId && !!taskId,
  })
}

export function useBenchSessionByArtifact(benchRunId: string, artifactId: string | null) {
  return useQuery({
    queryKey: ['bench-session-artifact', benchRunId, artifactId],
    queryFn: () => api.bench.sessionByArtifact(benchRunId, artifactId ?? ''),
    enabled: !!benchRunId && !!artifactId,
  })
}

export function useBenchAdminSummary(params?: Parameters<typeof api.bench.admin.summary>[0]) {
  return useQuery({
    queryKey: ['bench-admin-summary', params],
    queryFn: () => api.bench.admin.summary(params),
  })
}

export function useBenchAdminDaily(params?: Parameters<typeof api.bench.admin.daily>[0]) {
  return useQuery({
    queryKey: ['bench-admin-daily', params],
    queryFn: () => api.bench.admin.daily(params),
  })
}

export function useBenchAdminSamples(params?: Parameters<typeof api.bench.admin.samples>[0] & { enabled?: boolean }) {
  const { enabled, ...queryParams } = params ?? {}
  return useQuery({
    queryKey: ['bench-admin-samples', queryParams],
    queryFn: () => api.bench.admin.samples(queryParams),
    enabled: enabled !== false,
  })
}

export function useBenchAdminDomains(params?: Parameters<typeof api.bench.admin.domains>[0]) {
  return useQuery({
    queryKey: ['bench-admin-domains', params],
    queryFn: () => api.bench.admin.domains(params),
  })
}

export function useBenchAdminTags(includeArchived = false) {
  return useQuery({
    queryKey: ['bench-admin-tags', includeArchived],
    queryFn: () => api.bench.admin.tags(includeArchived),
  })
}

export function useCreateBenchAdminTag() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: Parameters<typeof api.bench.admin.createTag>[0]) => api.bench.admin.createTag(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['bench-admin-tags'] })
    },
  })
}

export function useUpdateBenchAdminTag() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ tagId, input }: { tagId: string; input: Parameters<typeof api.bench.admin.updateTag>[1] }) =>
      api.bench.admin.updateTag(tagId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['bench-admin-tags'] })
      void queryClient.invalidateQueries({ queryKey: ['bench-admin-samples'] })
      void queryClient.invalidateQueries({ queryKey: ['bench-admin-domains'] })
    },
  })
}

export function useAddBenchDomainTags() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: Parameters<typeof api.bench.admin.addDomainTags>[0]) => api.bench.admin.addDomainTags(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['bench-admin-samples'] })
      void queryClient.invalidateQueries({ queryKey: ['bench-admin-domains'] })
    },
  })
}

export function useRemoveBenchDomainTags() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: Parameters<typeof api.bench.admin.removeDomainTags>[0]) => api.bench.admin.removeDomainTags(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['bench-admin-samples'] })
      void queryClient.invalidateQueries({ queryKey: ['bench-admin-domains'] })
    },
  })
}

export function useExportBenchTemplates() {
  return useMutation({
    mutationFn: (params: Parameters<typeof api.bench.admin.exportTemplates>[0]) => api.bench.admin.exportTemplates(params),
  })
}

export function useCreateBenchResults() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ benchRunId, input }: { benchRunId: string; input: Parameters<typeof api.bench.createResults>[1] }) =>
      api.bench.createResults(benchRunId, input),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['bench-results', variables.benchRunId] })
      void queryClient.invalidateQueries({ queryKey: ['bench-run', variables.benchRunId] })
    },
  })
}

// --- Session Diagnosis Hooks ---

export function useSessionDiagnosis() {
  return useMutation({
    mutationFn: (params: { url?: string; sessionId?: string }) =>
      api.sessionDiagnosis.diagnose(params),
  })
}

// --- Flow Control Hooks ---

export function useFlowControlSlots(params?: {
  instance_id?: string
  scope_key?: string
  flow_id?: string
  limit?: number
  offset?: number
}) {
  return useQuery({
    queryKey: ['flow-control-slots', params],
    queryFn: () => api.flowControl.listSlots(params),
  })
}

export function useFlowControlSlotInstances() {
  return useQuery({
    queryKey: ['flow-control-slot-instances'],
    queryFn: () => api.flowControl.slotInstances(),
  })
}

export function useDeleteFlowControlSlot() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, cascade }: { id: number; cascade?: boolean }) =>
      api.flowControl.deleteSlot(id, cascade),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flow-control-slots'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-slot-instances'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-queue'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-queue-instances'] })
    },
  })
}

export function useBatchDeleteFlowControlSlots() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (ids: number[]) => api.flowControl.batchDeleteSlots(ids),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flow-control-slots'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-slot-instances'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-queue'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-queue-instances'] })
    },
  })
}

export function useDeleteAllFlowControlSlots() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (instanceId?: string) => api.flowControl.deleteAllSlots(instanceId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flow-control-slots'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-slot-instances'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-queue'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-queue-instances'] })
    },
  })
}

export function useFlowControlQueue(params?: {
  instance_id?: string
  scope_key?: string
  flow_id?: string
  status?: string
  limit?: number
  offset?: number
}) {
  return useQuery({
    queryKey: ['flow-control-queue', params],
    queryFn: () => api.flowControl.listQueue(params),
  })
}

export function useFlowControlQueueInstances() {
  return useQuery({
    queryKey: ['flow-control-queue-instances'],
    queryFn: () => api.flowControl.queueInstances(),
  })
}

export function useDeleteFlowControlQueueItem() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, cascade }: { id: number; cascade?: boolean }) =>
      api.flowControl.deleteQueueItem(id, cascade),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flow-control-queue'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-queue-instances'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-slots'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-slot-instances'] })
    },
  })
}

export function useBatchDeleteFlowControlQueueItems() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (ids: number[]) => api.flowControl.batchDeleteQueueItems(ids),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flow-control-queue'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-queue-instances'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-slots'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-slot-instances'] })
    },
  })
}

export function useDeleteAllFlowControlQueueItems() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ instanceId, status }: { instanceId?: string; status?: string }) =>
      api.flowControl.deleteAllQueueItems(instanceId, status),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flow-control-queue'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-queue-instances'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-slots'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-slot-instances'] })
    },
  })
}

export function useDeleteFlowControlFlow() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (flowId: string) => api.flowControl.deleteFlow(flowId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flow-control-slots'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-slot-instances'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-queue'] })
      void queryClient.invalidateQueries({ queryKey: ['flow-control-queue-instances'] })
    },
  })
}

// --- Theta Reasoning Log Hooks ---

export function useThetaTraceLogs(params: ThetaTraceQueryParams & { _ctoken?: string; _iamToken?: string; _cookies?: string }) {
  const isAsync = params.type === 'async'
  return useQuery({
    queryKey: ['theta-trace-logs', params],
    queryFn: () => isAsync ? api.theta.asyncTraceQuery(params) : api.theta.traceQuery(params),
    enabled: !!params._ctoken && (!!params._iamToken || !!params._cookies) && params.appKeyId !== undefined && params.appKeyId > 0,
    retry: 1,
  })
}

/** Extract total count from theta API response (supports both success and error shapes) */
export function getThetaTotal(data: unknown): number {
  if (!data || typeof data !== 'object') return 0
  const obj = data as Record<string, unknown>
  if (typeof obj.total === 'number') return obj.total
  if (Array.isArray(obj.data)) return obj.data.length
  return 0
}

export function useThetaTokenList(ctoken?: string, iamToken?: string, cookies?: string) {
  return useQuery({
    queryKey: ['theta-token-list', ctoken, iamToken],
    queryFn: () => api.theta.tokenList(ctoken, iamToken, cookies),
    enabled: !!ctoken && (!!iamToken || !!cookies),
    retry: 1,
  })
}

// --- Human Intervention Hooks ---

export function useInterventions(flowId: string) {
  return useQuery({
    queryKey: ['interventions', flowId],
    queryFn: () => api.runs.interventions(flowId),
    enabled: !!flowId,
  })
}

export function useIntervene(flowId: string) {
  const queryClient = useQueryClient()
  return useMutation<InterveneResult, Error, InterveneRequest>({
    mutationFn: (req: InterveneRequest) => api.runs.intervene(flowId, req),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['run', flowId] })
      void queryClient.invalidateQueries({ queryKey: ['interventions', flowId] })
      void queryClient.invalidateQueries({ queryKey: ['events', flowId] })
      void queryClient.invalidateQueries({ queryKey: ['nodes', flowId] })
    },
  })
}

export function useUpdateSession(flowId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (patch: SessionInfoUpdate) => api.runs.updateSession(flowId, patch),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['interventions', flowId] })
      void queryClient.invalidateQueries({ queryKey: ['run', flowId] })
    },
  })
}

// --- Node Step Trace Hooks ---

export function useNodeStepTraces(flowId: string, nodeId: string | null, attempt = 1) {
  return useQuery<NodeStepTraceData>({
    queryKey: ['node-step-traces', flowId, nodeId, attempt],
    queryFn: () => api.stepTraces.get(flowId, nodeId!, attempt),
    enabled: !!flowId && !!nodeId,
  })
}

// --- Hallucination Check Hooks ---

export function useHallucinationChecks(flowId: string, nodeId: string | null, attempt = 1) {
  return useQuery<HallucinationCheckData>({
    queryKey: ['hallucination-checks', flowId, nodeId, attempt],
    queryFn: () => api.hallucinationChecks.get(flowId, nodeId!, attempt),
    enabled: !!flowId && !!nodeId,
  })
}

// --- Auto-Heal Hooks ---

/** Poll interval for checking diagnosis status (ms) */
const DIAGNOSIS_POLL_INTERVAL = 3000
/** Max poll attempts before giving up (15 min at 3s interval ≈ 300 attempts) */
const DIAGNOSIS_MAX_POLL_ATTEMPTS = 300

/**
 * Async diagnose hook: submits diagnosis request, then polls until complete.
 * Returns the final AutoHealDiagnosisResult when done.
 *
 * Usage:
 *   diagnoseMutation.mutate({ flowId, ... }, { onSuccess: (result) => ... })
 *   The result is the FINAL parsed diagnosis (not the submit response).
 */
export function useAutoHealDiagnose() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (params: { flowId: string; useBaas?: boolean; customPrompt?: string }): Promise<AutoHealDiagnosisResult> => {
      // Step 1: Submit — returns immediately with diagnosisId
      const submitResult = await api.autoHeal.diagnose(params)

      // Backward compat: if the server is still running old synchronous code,
      // POST /diagnose returns the full result directly (has `ok` + `diagnosisId` + `summary`)
      if ('summary' in submitResult && !('status' in submitResult)) {
        return submitResult as unknown as AutoHealDiagnosisResult
      }

      // New async flow: brief pause before first poll — let the server store the session
      await new Promise((r) => setTimeout(r, 1000))

      // Step 2: Poll until completed or failed
      for (let attempt = 0; attempt < DIAGNOSIS_MAX_POLL_ATTEMPTS; attempt++) {
        try {
          const pollResult = await api.autoHeal.pollDiagnosis(submitResult.diagnosisId)

          if (pollResult.status === 'completed' && pollResult.result) {
            return pollResult.result
          }

          if (pollResult.status === 'failed') {
            throw new Error(pollResult.error ?? '诊断失败')
          }

          // pending or running — wait and retry
        } catch (err) {
          // 404 means the server hasn't stored the session yet (race) — keep polling
          if (err instanceof Error && err.message.startsWith('API 404')) {
            // treat as pending, continue polling
          } else {
            throw err
          }
        }

        await new Promise((r) => setTimeout(r, DIAGNOSIS_POLL_INTERVAL))
      }

      throw new Error('诊断超时，请稍后重试')
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
      void queryClient.invalidateQueries({ queryKey: ['workflow-types'] })
    },
  })
}

export function useAutoHealApply() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (params: { workflowId: string; spec: WorkflowSpec; packId?: string; diagnosisId?: string; autoRun?: boolean }) =>
      api.autoHeal.apply(params),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['db-workflows'] })
      void queryClient.invalidateQueries({ queryKey: ['workflow-types'] })
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
    },
  })
}

export function useAutoHealRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (params: { workflowId: string; params?: Record<string, unknown> }) =>
      api.autoHeal.run(params),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
      void queryClient.invalidateQueries({ queryKey: ['workflow-types'] })
    },
  })
}

// ── Smart Onboarding ──────────────────────────────────────────────────

/** Poll interval for smart onboarding generation (3 seconds) */
const SMART_ONBOARDING_POLL_INTERVAL = 3000

/** Max poll attempts before giving up (15 min at 3s interval ≈ 300 attempts) */
const SMART_ONBOARDING_MAX_POLL_ATTEMPTS = 300

/**
 * Async generate hook: submits YAML generation request, then polls until complete.
 * Returns the final generation result when done, including baasSessionId for multi-turn.
 */
export function useSmartOnboardingGenerate() {
  return useMutation({
    mutationFn: async (params: SmartOnboardingGenerateRequest): Promise<SmartOnboardingGenerationStatus & { baasSessionId?: string }> => {
      // Step 1: Submit — returns immediately with generationId
      const submitResult = await api.smartOnboarding.generate(params)

      // Brief pause before first poll — let the server store the session
      await new Promise((r) => setTimeout(r, 1000))

      // Step 2: Poll until completed or failed
      for (let attempt = 0; attempt < SMART_ONBOARDING_MAX_POLL_ATTEMPTS; attempt++) {
        try {
          const pollResult = await api.smartOnboarding.pollGeneration(submitResult.generationId)

          if (pollResult.status === 'completed' || pollResult.status === 'failed') {
            return { ...pollResult, baasSessionId: submitResult.sessionId }
          }

          // pending or running — wait and retry
        } catch (err) {
          // 404 means the server hasn't stored the session yet (race) — keep polling
          if (err instanceof Error && err.message.startsWith('API 404')) {
            // treat as pending, continue polling
          } else {
            throw err
          }
        }

        await new Promise((r) => setTimeout(r, SMART_ONBOARDING_POLL_INTERVAL))
      }

      throw new Error('生成超时，请稍后重试')
    },
  })
}

export function useSmartOnboardingTestRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (params: SmartOnboardingTestRunRequest) =>
      api.smartOnboarding.testRun(params),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
      void queryClient.invalidateQueries({ queryKey: ['workflow-types'] })
    },
  })
}

export function useSmartOnboardingValidate() {
  return useMutation({
    mutationFn: (params: SmartOnboardingValidateRequest): Promise<SmartOnboardingValidateResult> =>
      api.smartOnboarding.validate(params),
  })
}

export function useSmartOnboardingSave() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (params: {
      workflowId: string
      spec: unknown
      facade?: { command?: string; remark?: string }
      botOwnerId?: string
    }) =>
      api.workflows.save(params.workflowId, params.spec as WorkflowSpec, {
        facade: params.facade,
        botOwnerId: params.botOwnerId,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['db-workflows'] })
      void queryClient.invalidateQueries({ queryKey: ['workflow-types'] })
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
    },
  })
}

// ── App Config ──

export function useAppConfigs(enabledOnly = false) {
  return useQuery({
    queryKey: ['app-configs', enabledOnly],
    queryFn: () => api.appConfig.list(enabledOnly),
  })
}

export function useCreateAppConfig() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: AppConfigCreateInput) => api.appConfig.create(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-configs'] })
    },
  })
}

export function useUpdateAppConfig() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ configKey, input }: { configKey: string; input: AppConfigUpdateInput }) =>
      api.appConfig.update(configKey, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-configs'] })
    },
  })
}

export function useDeleteAppConfig() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (configKey: string) => api.appConfig.delete(configKey),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-configs'] })
    },
  })
}

// ── Admin Users ──

export function useAdminUsers() {
  return useQuery<AdminUsersListResponse>({
    queryKey: ['admin-users'],
    queryFn: () => api.adminUsers.list(),
  })
}

export function useCreateAdminUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: AdminUserCreateInput) => api.adminUsers.create(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
    },
  })
}

export function useDeleteAdminUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.adminUsers.delete(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
    },
  })
}

// ── Run Archive ──

export function useRunArchive(flowId: string | null) {
  return useQuery({
    queryKey: ['run-archive', flowId],
    queryFn: () => api.runArchives.get(flowId!),
    enabled: !!flowId,
    staleTime: 0,
  })
}

// ── Workflow Node Analytics ──

export function useWorkflowNodeStats(workflowId: string | null, days?: number) {
  return useQuery({
    queryKey: ['workflow-node-stats', workflowId, days],
    queryFn: () => api.workflowNodeStats.get(workflowId!, days),
    enabled: !!workflowId,
    staleTime: 30_000,
  })
}

export function useWorkflowHealth(workflowId: string | null, days?: number) {
  return useQuery({
    queryKey: ['workflow-health', workflowId, days],
    queryFn: () => api.workflowNodeStats.getHealth(workflowId!, days),
    enabled: !!workflowId,
    staleTime: 30_000,
  })
}

export function useWorkflowHealthTrend(workflowId: string | null, days = 7) {
  return useQuery({
    queryKey: ['workflow-health-trend', workflowId, days],
    queryFn: async () => {
      const res = await fetch(`/api/workflows/${encodeURIComponent(workflowId!)}/health-trend?days=${days}`)
      if (!res.ok) return []
      const json = await res.json()
      return (json.data ?? []) as Array<{ snapshot_date: string; overall_score: number; success_rate: number }>
    },
    enabled: !!workflowId,
    staleTime: 300_000,
  })
}

// ── Workflow History & Lifecycle ──

export function useWorkflowHistory(workflowId: string | null, limit = 20) {
  return useQuery({
    queryKey: ['workflow-history', workflowId, limit],
    queryFn: () => api.workflows.getHistory(workflowId!, limit),
    enabled: !!workflowId,
    staleTime: 60_000,
  })
}

export function useWorkflowAccess(workflowId: string | null) {
  return useQuery({
    queryKey: ['workflow-access', workflowId],
    queryFn: () => api.workflows.getAccess(workflowId!),
    enabled: !!workflowId,
    staleTime: 30_000,
  })
}

export function useWorkflowAutoAnalysis(workflowId: string | null) {
  return useQuery({
    queryKey: ['workflow-auto-analysis', workflowId],
    queryFn: () => api.taskGuard.getAutoAnalysis(workflowId!),
    enabled: !!workflowId,
    staleTime: 15_000,
  })
}

export function useUpdateWorkflowAutoAnalysis() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ workflowId, enabled }: { workflowId: string; enabled: boolean }) =>
      api.taskGuard.updateAutoAnalysis(workflowId, enabled),
    onSuccess: (setting) => {
      queryClient.setQueryData(['workflow-auto-analysis', setting.workflowId], setting)
    },
  })
}

export function useDeleteWorkflow() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (workflowId: string) => api.workflows.delete(workflowId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workflow-types'] })
      void queryClient.invalidateQueries({ queryKey: ['db-workflows'] })
      void queryClient.invalidateQueries({ queryKey: ['workflows-page'] })
    },
  })
}

export function useRestoreWorkflowVersion() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ workflowId, version }: { workflowId: string; version: number }) => {
      const snapshot = await api.workflows.getVersion(workflowId, version)
      const spec = JSON.parse(snapshot.specJson) as import('@avernet/clawweb-shared/web/types').WorkflowSpec
      return api.workflows.save(workflowId, spec)
    },
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['workflow-history', variables.workflowId] })
      void queryClient.invalidateQueries({ queryKey: ['db-workflow', variables.workflowId] })
      void queryClient.invalidateQueries({ queryKey: ['workflow-types'] })
    },
  })
}
