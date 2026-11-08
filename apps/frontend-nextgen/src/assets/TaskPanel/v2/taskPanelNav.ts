// @asset-migrated: teamclaw 自研资产
/**
 * 资产层导航契约(src/assets 不 import stores,宿主经此结构注入,P4 会话联动由宿主消费 pendingNavigation)。
 * 类型与业务层 useTaskPanelNavBinding 的返回值 structural 对齐(Row1 裁决)。
 */
import { useCallback, useState } from 'react';

import type { ResolvedSessionTarget } from '../sessionResolution';

/** 资产层导航状态记录(与业务层 store 的 TaskNavRecord structural 对齐,不跨界 import) */
export interface TaskNavRecord {
  /** 下钻栈:空 = Level1;顶 = 当前 Level2 节点 id */
  drillStack: string[];
  selectedNodeId: string | null;
}

export type { ResolvedSessionTarget };

export interface TaskPanelNav {
  selectedNodeId: string | null;
  /** 下钻栈:空 = Level1;顶 = 当前 Level2 节点 id */
  drillStack: string[];
  /** 选中任务根节点并恢复宿主当前会话，不打开任务联动会话。 */
  selectRootNode: (nodeId: string) => void;
  selectNode: (nodeId: string, target: ResolvedSessionTarget | null) => void;
  drillInto: (nodeId: string, target: ResolvedSessionTarget | null) => void;
  backToOverview: () => void;
}

/** 下钻封顶 2 层(与 store 同源常量;L2 无再下钻热区,防御性封顶) */
const MAX_DRILL_DEPTH = 2;
const emptyRecord: TaskNavRecord = { drillStack: [], selectedNodeId: null };

/**
 * nav 未注入时的本地降级(直接以 assets 组件独立使用/单测场景)。
 * 语义与 taskSidePanelStore 一致:root/select 记录选中;drill 去重 push 封顶;返回清栈不清选中。
 * 本地降级无宿主消费会话跳转,target 参数仅保形不落地。
 */
export function useLocalTaskPanelNav(): TaskPanelNav {
  const [record, setRecord] = useState<TaskNavRecord>(emptyRecord);

  const selectRootNode = useCallback((nodeId: string) => {
    setRecord((r) => ({ ...r, selectedNodeId: nodeId }));
  }, []);

  const selectNode = useCallback((nodeId: string, target: ResolvedSessionTarget | null) => {
    void target; // 本地降级无宿主消费跳转,target 仅保形
    setRecord((r) => ({ ...r, selectedNodeId: nodeId }));
  }, []);

  const drillInto = useCallback((nodeId: string, target: ResolvedSessionTarget | null) => {
    void target;
    setRecord((r) => ({
      ...r,
      drillStack: r.drillStack.includes(nodeId)
        ? r.drillStack
        : r.drillStack.length >= MAX_DRILL_DEPTH
          ? r.drillStack
          : [...r.drillStack, nodeId],
    }));
  }, []);

  const backToOverview = useCallback(() => {
    setRecord((r) => ({ ...r, drillStack: [] }));
  }, []);

  return {
    selectedNodeId: record.selectedNodeId,
    drillStack: record.drillStack,
    selectRootNode,
    selectNode,
    drillInto,
    backToOverview,
  };
}
