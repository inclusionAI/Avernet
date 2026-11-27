import { Button, Input, Textarea } from '@/components/ui';
import type { BotTeamNode, TodoBotOption } from '@/domain/todos/models';
import { ArrowLeft, Pencil } from 'lucide-react';
import { useEffect, useState } from 'react';
import { BotTeamNodeEditor } from './BotTeamNodeEditor';
import { BotTeamWorkflow } from './BotTeamWorkflow';
import type { BotTeamEditorState } from './botTeamEditorModel';
import { emptyBotTeamNode, formatBotTeamDateTime } from './botTeamEditorModel';

interface BotTeamEditorProps {
  editor: BotTeamEditorState;
  bots: TodoBotOption[];
  loading: boolean;
  onChange: (next: BotTeamEditorState) => void;
  onBack: () => void;
  onEdit: () => void;
  onSave: () => Promise<void>;
}

function BotTeamMetadata({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="inline text-muted-foreground">{label}:</dt>{' '}
      <dd className="m-0 inline text-foreground/85">{value}</dd>
    </div>
  );
}

function BotTeamBasics({
  editor,
  editable,
  onChange,
}: {
  editor: BotTeamEditorState;
  editable: boolean;
  onChange: (next: BotTeamEditorState) => void;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <h3 className="m-0 mb-3 text-sm font-semibold text-foreground">基本信息</h3>
      {editable ? (
        <div className="grid gap-4 md:grid-cols-2">
          <label className="space-y-1.5 text-xs font-medium text-foreground">
            <span>名称</span>
            <Input value={editor.name} onChange={(event) => onChange({ ...editor, name: event.target.value })} placeholder="如：Coding 小队" />
          </label>
          <label className="space-y-1.5 text-xs font-medium text-foreground md:col-span-2">
            <span>描述</span>
            <Textarea
              value={editor.description}
              onChange={(event) => onChange({ ...editor, description: event.target.value })}
              placeholder="小队擅长场景与节点能力概要"
              rows={3}
            />
          </label>
        </div>
      ) : (
        <div className="space-y-2">
          <dl className="m-0 grid grid-cols-[72px_minmax(0,1fr)] gap-x-4 gap-y-2 text-xs md:grid-cols-[72px_minmax(0,1fr)_72px_minmax(0,2fr)]">
            <dt className="text-muted-foreground">名称:</dt>
            <dd className="m-0 truncate font-medium text-foreground">{editor.name}</dd>
            <dt className="text-muted-foreground">描述:</dt>
            <dd className="m-0 truncate text-foreground/85">{editor.description || '—'}</dd>
          </dl>
          <dl className="m-0 grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2 lg:grid-cols-5">
            <BotTeamMetadata label="类型" value="工作流" />
            <BotTeamMetadata label="创建时间" value={editor.createdAt ? formatBotTeamDateTime(editor.createdAt) : '—'} />
            <BotTeamMetadata label="更新时间" value={editor.updatedAt ? formatBotTeamDateTime(editor.updatedAt) : '—'} />
            <BotTeamMetadata label="创建人" value={editor.createdBy || '—'} />
            <BotTeamMetadata label="更新人" value={editor.updatedBy || '—'} />
          </dl>
        </div>
      )}
    </div>
  );
}

export function BotTeamEditor({ editor, bots, loading, onChange, onBack, onEdit, onSave }: BotTeamEditorProps) {
  const editable = editor.mode !== 'view';
  const [selectedNodeIndex, setSelectedNodeIndex] = useState(0);
  const selectedNode = editor.nodes[selectedNodeIndex];

  useEffect(() => {
    setSelectedNodeIndex((current) => Math.max(0, Math.min(current, editor.nodes.length - 1)));
  }, [editor.nodes.length]);

  const updateNode = (index: number, update: Partial<BotTeamNode>) => {
    onChange({
      ...editor,
      nodes: editor.nodes.map((node, nodeIndex) => (nodeIndex === index ? { ...node, ...update } : node)),
    });
  };

  const addNodeAfter = (index: number) => {
    const nodes = [...editor.nodes];
    const insertAt = Math.min(index + 1, nodes.length);
    nodes.splice(insertAt, 0, emptyBotTeamNode(nodes, bots[0]?.botId));
    onChange({ ...editor, nodes });
    setSelectedNodeIndex(insertAt);
  };

  const removeNode = (index: number) => {
    onChange({ ...editor, nodes: editor.nodes.filter((_, itemIndex) => itemIndex !== index) });
    setSelectedNodeIndex((current) => (current > index ? current - 1 : Math.min(current, editor.nodes.length - 2)));
  };

  const moveNode = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= editor.nodes.length) return;
    const nodes = [...editor.nodes];
    [nodes[index], nodes[target]] = [nodes[target], nodes[index]];
    onChange({ ...editor, nodes });
    setSelectedNodeIndex(target);
  };

  const valid = Boolean(
    editor.name.trim() &&
      editor.nodes.length > 0 &&
      editor.nodes.every((node) => node.name.trim() && node.code.trim() && node.botId),
  );

  return (
    <section className="space-y-5">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" aria-label="返回 Bot 小队列表" onClick={onBack}>
          <ArrowLeft className="size-4" aria-hidden />
        </Button>
        <div className="min-w-0">
          <h2 className="m-0 truncate text-lg font-semibold text-foreground">{editor.name || '创建 Bot 小队'}</h2>
          <p className="m-0 mt-0.5 text-xs text-muted-foreground">按节点从左到右线性执行</p>
        </div>
        {editor.mode === 'view' ? (
          <Button className="ml-auto" onClick={onEdit} leftIcon={<Pencil className="size-3.5" aria-hidden />}>
            编辑
          </Button>
        ) : (
          <Button className="ml-auto" loading={loading} disabled={!valid} onClick={() => void onSave()}>
            {editor.mode === 'create' ? '创建并发布' : '保存并发布新版本'}
          </Button>
        )}
      </div>

      <BotTeamBasics editor={editor} editable={editable} onChange={onChange} />
      <BotTeamWorkflow
        editor={editor}
        bots={bots}
        editable={editable}
        selectedNodeIndex={selectedNodeIndex}
        onChange={onChange}
        onSelectNode={setSelectedNodeIndex}
        onAddNodeAfter={addNodeAfter}
        onRemoveNode={removeNode}
      />
      <BotTeamNodeEditor
        node={selectedNode}
        nodeIndex={selectedNodeIndex}
        nodeCount={editor.nodes.length}
        bots={bots}
        editable={editable}
        onUpdate={(update) => updateNode(selectedNodeIndex, update)}
        onMove={(direction) => moveNode(selectedNodeIndex, direction)}
      />
    </section>
  );
}
