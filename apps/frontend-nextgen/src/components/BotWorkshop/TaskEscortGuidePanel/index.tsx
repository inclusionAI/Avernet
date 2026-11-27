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
          任务护航把 Bot 的工作流运行记录集中呈现在一个工作台里：按工作流查看每次运行的节点状态、耗时与输出，
          定位失败原因，并从运行详情跳转到相关问题继续排查。本期以「只读观察」口径透出；未接通的自动修复、
          重跑、审批等能力会标注「能力建设中」，不会给出可点击但无响应的入口。
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
            <span className="font-medium text-[var(--color-fg)]">选择工作流</span>
            ：在「日志分析」页签选择要观察的工作流。
          </li>
          <li>
            <span className="font-medium text-[var(--color-fg)]">查看运行概览</span>
            ：查看该工作流的运行状态、近期表现与运行列表。
          </li>
          <li>
            <span className="font-medium text-[var(--color-fg)]">深入单次运行</span>
            ：打开运行详情查看节点、日志与档案，并从「查看相关问题」进入问题与优化页。
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
            <p className="mt-0.5">点击失败记录查看具体节点错误信息；自动重试能力建设中，当前仅做只读观察。</p>
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
