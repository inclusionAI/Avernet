import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import React from 'react';

const TaskEscortGuidePanel: React.FC = () => (
  <div className="space-y-4 text-sm">
    <Card className="bg-muted p-0 shadow-none">
      <CardHeader className="p-4 pb-0">
        <CardTitle className="text-sm">什么是任务护航？</CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-2">
        <p className="text-[var(--color-muted)]">
          任务护航（HarnessFlow）为 Bot 提供可视化的工作流编排与执行监控能力。
          您可以配置多步骤的任务流程，实时查看每一步的执行状态、耗时和输出， 快速定位失败原因。
        </p>
      </CardContent>
    </Card>
    <Card className="bg-muted p-0 shadow-none">
      <CardHeader className="p-4 pb-0">
        <CardTitle className="text-sm">快速上手</CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-2">
        <ol className="list-decimal space-y-2 pl-5 text-[var(--color-muted)]">
          <li>
            <span className="font-medium text-[var(--color-fg)]">配置工作流</span>
            ：在「流程配置」面板中定义工作流节点和执行逻辑。
          </li>
          <li>
            <span className="font-medium text-[var(--color-fg)]">触发执行</span>
            ：通过手动触发、定时任务或事件回调启动工作流。
          </li>
          <li>
            <span className="font-medium text-[var(--color-fg)]">监控分析</span>
            ：在「日志分析」面板中查看运行记录、节点状态和详细日志。
          </li>
        </ol>
      </CardContent>
    </Card>
    <Card className="bg-muted p-0 shadow-none">
      <CardHeader className="p-4 pb-0">
        <CardTitle className="text-sm">常见问题</CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-2">
        <div className="space-y-3 text-[var(--color-muted)]">
          <div>
            <p className="font-medium text-[var(--color-fg)]">工作流执行失败怎么办？</p>
            <p className="mt-0.5">点击失败记录查看具体节点错误信息，根据错误提示调整节点配置后重试。</p>
          </div>
          <div>
            <p className="font-medium text-[var(--color-fg)]">如何查看节点详细日志？</p>
            <p className="mt-0.5">在运行记录中展开具体执行，可以查看每个节点的输入输出、耗时和 Token 消耗。</p>
          </div>
        </div>
      </CardContent>
    </Card>
  </div>
);

export default TaskEscortGuidePanel;
