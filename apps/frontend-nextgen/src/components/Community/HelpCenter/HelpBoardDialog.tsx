import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Checkbox';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Textarea } from '@/components/ui/Textarea';
import {
  HELP_ROLES,
  HELP_SOURCES,
  type HelpBoard,
  type HelpBoardDraft,
  type HelpBot,
  type HelpRole,
} from '@/domain/communityHelp/types';
import { cn } from '@/utils/cn';
import { Bot, Check } from 'lucide-react';
import { useState } from 'react';

interface Props {
  board: HelpBoard | null;
  bots: HelpBot[];
  onClose: () => void;
  onSave: (draft: HelpBoardDraft, editingId?: string) => boolean;
}

export function HelpBoardDialog({ board, bots, onClose, onSave }: Props) {
  const [draft, setDraft] = useState<HelpBoardDraft>(() =>
    board
      ? {
          name: board.name,
          description: board.description,
          members: board.members.map((member) => ({ ...member, roles: [...member.roles] })),
          sources: [...board.sources],
        }
      : { name: '', description: '', members: [], sources: [...HELP_SOURCES] },
  );

  const toggleBot = (botId: string, checked: boolean) =>
    setDraft((value) => ({
      ...value,
      members: checked
        ? [...value.members, { botId, roles: [] }]
        : value.members.filter((member) => member.botId !== botId),
    }));
  const toggleRole = (botId: string, role: HelpRole) =>
    setDraft((value) => ({
      ...value,
      members: value.members.map((member) =>
        member.botId !== botId
          ? member
          : {
              ...member,
              roles: member.roles.includes(role)
                ? member.roles.filter((item) => item !== role)
                : [...member.roles, role],
            },
      ),
    }));

  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <ModalContent size="lg">
        <ModalHeader>
          <ModalTitle>{board ? '版面设置' : '新增版面'}</ModalTitle>
          <ModalDescription>
            配置 Mock 协作团队与任务来源。当前仅支持 AiCoding 类型，不会调用真实 Bot。
          </ModalDescription>
        </ModalHeader>
        <form
          className="space-y-5"
          onSubmit={(event) => {
            event.preventDefault();
            if (onSave(draft, board?.id)) onClose();
          }}
        >
          <div className="grid gap-4 sm:grid-cols-[1fr_140px]">
            <label className="space-y-2 text-xs font-medium">
              <span>
                版面名称 <span className="text-destructive">*</span>
              </span>
              <Input
                autoFocus
                required
                maxLength={40}
                value={draft.name}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                placeholder="例如：AiCoding · 前端协作"
              />
            </label>
            <label className="space-y-2 text-xs font-medium">
              <span>版面类型</span>
              <Input value="AiCoding" disabled />
            </label>
          </div>
          <label className="block space-y-2 text-xs font-medium">
            <span>版面说明</span>
            <Textarea
              maxLength={300}
              rows={2}
              value={draft.description}
              onChange={(event) => setDraft({ ...draft, description: event.target.value })}
              placeholder="这个版面主要解决什么问题？"
            />
          </label>
          <fieldset className="space-y-2">
            <legend className="mb-2 text-xs font-medium">
              任务来源 <span className="text-destructive">*</span>
            </legend>
            <div className="flex flex-wrap gap-4">
              {HELP_SOURCES.map((source) => (
                <label key={source} className="flex items-center gap-2 text-xs">
                  <Checkbox
                    checked={draft.sources.includes(source)}
                    onCheckedChange={(checked) =>
                      setDraft((value) => ({
                        ...value,
                        sources: checked ? [...value.sources, source] : value.sources.filter((item) => item !== source),
                      }))
                    }
                  />
                  {source}
                </label>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">新建任务时从已启用的来源中选择，已有任务的来源保持不变。</p>
          </fieldset>
          <fieldset className="space-y-2">
            <legend className="text-xs font-medium">
              协作 Bot <span className="text-destructive">*</span> · 已选 {draft.members.length} 个
            </legend>
            <p className="mb-3 text-xs text-muted-foreground">
              先圈选 Bot，再指定一个或多个角色。任务按照配置角色的顺序协作。
            </p>
            <div className="space-y-2">
              {bots.map((bot) => {
                const member = draft.members.find((item) => item.botId === bot.id);
                return (
                  <div
                    key={bot.id}
                    className={cn(
                      'rounded-xl border p-3',
                      member ? 'border-primary/30 bg-primary/5' : 'border-border bg-card',
                    )}
                  >
                    <label className="flex cursor-pointer items-center gap-3">
                      <Checkbox checked={Boolean(member)} onCheckedChange={(checked) => toggleBot(bot.id, checked)} />
                      <span className="flex size-8 items-center justify-center rounded-lg bg-muted text-primary">
                        <Bot className="size-4" aria-hidden />
                      </span>
                      <span className="min-w-0">
                        <span className="block text-xs font-medium">{bot.name}</span>
                        <span className="text-xs text-muted-foreground">{bot.description}</span>
                      </span>
                    </label>
                    {member && (
                      <div className="mt-3 flex flex-wrap gap-1.5 pl-7" role="group" aria-label={`${bot.name}的角色`}>
                        {HELP_ROLES.map((role) => (
                          <Button
                            key={role}
                            variant={member.roles.includes(role) ? 'default' : 'outline'}
                            size="sm"
                            className="h-7 px-2 text-xs"
                            aria-label={`${bot.name} · ${role}`}
                            aria-pressed={member.roles.includes(role)}
                            onClick={() => toggleRole(bot.id, role)}
                          >
                            {member.roles.includes(role) && <Check className="size-3" aria-hidden />}
                            {role}
                          </Button>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </fieldset>
          <ModalFooter className="sticky -bottom-6 border-t border-border bg-background py-4">
            <Button variant="outline" onClick={onClose}>
              取消
            </Button>
            <Button type="submit">{board ? '保存设置' : '创建版面'}</Button>
          </ModalFooter>
        </form>
      </ModalContent>
    </Modal>
  );
}
