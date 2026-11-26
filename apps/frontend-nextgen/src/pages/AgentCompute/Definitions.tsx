import { PageHeader } from '@/components/Common/PageHeader';
import { DefinitionLibrary } from '@/components/agentswarm/DefinitionLibrary';
import { resolveAgentswarmTenant } from '@/services/agentswarm/tenant';
import { history } from '@umijs/max';
import { useMemo } from 'react';

const BASE = '/agent-compute';

/**
 * 我的定义库, as a page of its own.
 *
 * The library outlives any single task — it is where agents are maintained
 * between runs — so it does not belong underneath a task's submit button.
 * Configuring a run reaches it through the picker instead.
 */
export default function AgentComputeDefinitionsPage() {
  const tenant = useMemo(() => resolveAgentswarmTenant(), []);

  return (
    <div className="flex h-full flex-col bg-background">
      <main className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
        <div className="flex w-full flex-col gap-5 px-5 py-6">
          <PageHeader
            title="我的定义库"
            description="保存可复用的 Agent 定义。配置计算任务时可从这里引用，空白新建的 Agent 也会保存到这里。"
            onBack={() => history.push(BASE)}
          />
          <DefinitionLibrary tenant={tenant} />
        </div>
      </main>
    </div>
  );
}