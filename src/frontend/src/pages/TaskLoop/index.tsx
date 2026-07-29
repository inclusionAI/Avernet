/**
 * TaskLoop 页 — 任务入口页 (Phase 4.5.1, plan §1.4b 分工置)。
 *
 * 路由 /bcn/task-loop/:taskId。副屏展示该任务的整体执行流程(顶层动态 DAG)。
 * 提需求入口:输入标题/背景 → POST /api/tasks → 跳转到新任务画布页
 * (FR-OBS-11: backend 同步发 panel 消息,群聊副屏也会弹出;此处页面内直接展开)。
 * 协作群节点下钻 → 跨页导航到该群页(/bcn/chat/detail),群页副屏展示群内 sub-DAG。
 */
import React, { useState } from 'react';
import { history, useParams } from '@umijs/max';

import TaskWorkflowView from '@/components/TaskWorkflowView';
// 副屏 panel 自注册(群聊侧 <AixUI panel> 消息命中时弹出画布) + openTaskPanel 命令式弹出
import { openTaskPanel } from '@/components/TaskWorkflowView/TaskPanel';
import '@/components/TaskWorkflowView/TaskPanel';
import { createTask, approveTask } from '@/services/backend-api/TaskController';

const TaskLoopPage: React.FC = () => {
  const params = useParams<{ taskId?: string }>();
  const taskId = params.taskId;
  const [title, setTitle] = useState('');
  const [background, setBackground] = useState('');
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleCreate = async () => {
    if (!title.trim()) {
      setErr('请输入需求标题');
      return;
    }
    setCreating(true);
    setErr(null);
    try {
      const res = await createTask({ title: title.trim(), background });
      // FR-OBS-11: 创建成功即弹出副屏(community profile 无 chat 推送总线,
      // 由前端创建流直接命中 chatBridge.openPanelTab;corp 接通 carrier transport
      // 后群聊侧 <AixUI panel> 推送与本调用收敛到同一 panel 渲染器)
      openTaskPanel(res.task_id, title.trim());
      // 跳转到新任务画布页;backend 已同步发 panel 消息触发群聊副屏弹出
      history.push(`/bcn/task-loop/${res.task_id}`);
    } catch (e: any) {
      setErr(e?.message ?? '创建任务失败');
    } finally {
      setCreating(false);
    }
  };

  const handleDrillDown = (groupId: string, _bcsRunId: string) => {
    // 路 A 主交互:跨页导航到协作群页,该页副屏展示群内 sub-DAG(现有画布不改)
    history.push(`/bcn/chat/detail?group=${groupId}`);
  };

  return (
    <div style={{ padding: 16, height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>目标驱动任务执行</h2>
        <div style={{ color: '#6b7280', fontSize: 12, marginTop: 4 }}>
          任务为一等公民;单Bot/协作群/BBS 各模态以节点呈现,deepresearch 动态拆解执行,整体生命周期可视化。
        </div>
      </div>

      {!taskId && (
        <div
          style={{
            border: '1px solid #e5e7eb',
            borderRadius: 8,
            padding: 12,
            marginBottom: 12,
            background: '#fff',
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 8 }}>提需求</div>
          <input
            style={inputStyle}
            placeholder="需求标题(必填)"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <textarea
            style={{ ...inputStyle, minHeight: 60, marginTop: 8 }}
            placeholder="背景说明(可选)"
            value={background}
            onChange={(e) => setBackground(e.target.value)}
          />
          {err && <div style={{ color: '#dc2626', fontSize: 12, marginTop: 4 }}>{err}</div>}
          <div style={{ marginTop: 8 }}>
            <button
              style={primaryBtn}
              disabled={creating}
              onClick={handleCreate}
            >
              {creating ? '创建中…' : '创建任务'}
            </button>
          </div>
        </div>
      )}

      {taskId && (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 8, alignItems: 'center' }}>
            <span style={{ fontWeight: 600, fontSize: 14 }}>任务 {taskId}</span>
            <button
              style={ghostBtn}
              onClick={() => {
                approveTask(taskId).catch(() => {});
              }}
            >
              启动执行 (approve)
            </button>
            <button
              style={ghostBtn}
              onClick={() => history.push('/bcn/task-loop')}
            >
              返回新建
            </button>
          </div>
          <div
            style={{
              flex: 1,
              border: '1px solid #e5e7eb',
              borderRadius: 8,
              overflow: 'hidden',
              background: '#fff',
              minHeight: 360,
            }}
          >
            <TaskWorkflowView taskId={taskId} onDrillDown={handleDrillDown} poll />
          </div>
        </div>
      )}
    </div>
  );
};

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '6px 8px',
  border: '1px solid #d1d5db',
  borderRadius: 6,
  fontSize: 13,
  boxSizing: 'border-box',
};

const primaryBtn: React.CSSProperties = {
  background: '#2563eb',
  color: '#fff',
  border: 'none',
  padding: '6px 14px',
  borderRadius: 6,
  fontSize: 13,
  cursor: 'pointer',
};

const ghostBtn: React.CSSProperties = {
  background: '#fff',
  color: '#2563eb',
  border: '1px solid #2563eb',
  padding: '4px 10px',
  borderRadius: 6,
  fontSize: 12,
  cursor: 'pointer',
};

export default TaskLoopPage;