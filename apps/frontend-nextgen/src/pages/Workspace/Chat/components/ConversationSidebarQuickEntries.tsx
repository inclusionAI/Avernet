// 分组标题行快捷入口组件集(自 ConversationSidebar 拆分,2026-11-05 TC-G005 文件体积门禁):
// - HeaderActionButton:动作型入口按钮——icon tooltip 与 aria-label 同文案(如「添加 Bot 好友」),
//   点击交装配层回调跳转(埋点在调用侧内聚);
// - CreateBotEntryButton:管理组「创建新 Bot」入口——「创建云端 Bot」确认弹窗(「目标空间」
//   下拉,选项序由装配层按侧栏切换序排好),确认收口后跳 Bot 管理页并默认唤起云端创建。
import {
  Button,
  Modal,
  ModalContent,
  ModalDescription,
  ModalFooter,
  ModalHeader,
  ModalTitle,
  ModalTrigger,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui';
import type { Space } from '@/domain/admin/models';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';
import { Plus, User, Users } from 'lucide-react';
import { useState } from 'react';

/** 分组标题行快捷入口按钮(2026-11-05 需求,同日验收微调文案与图标):图标区分动作语义
 *  (Plus=创建新 Bot/UserPlus=添加 Bot 好友),aria-label 与 tooltip 同文案。
 *  常驻显示不受分组内容/加载状态影响(空态文字引导与常驻入口互补并存)。 */
export function HeaderActionButton({
  label,
  icon: Icon,
  onClick,
}: {
  label: string;
  icon: typeof Plus;
  onClick(): void;
}) {
  return (
    <TooltipProvider delayDuration={0}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            aria-label={label}
            className="h-6 w-6 rounded-md text-content-icon hover:text-primary"
            onClick={onClick}
          >
            <Icon className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
        </TooltipTrigger>
        <TooltipContent>{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/** 创建入口(2026-11-05 需求,同日多轮验收微调):点击弹「创建云端 Bot」确认弹窗——先说明落地页
 *  为「Bot 管理」并要求选择 Bot 归属空间,确认后才交装配层带所选空间跳 Bot 管理页并默认唤起
 *  云端创建(装配层 ?create=cloud)。空间选择为「目标空间」下拉(三轮验收:列表按钮改 Select,
 *  选项顺序由装配层按侧栏切换序排好),默认选中当前空间,由确认收口不直接跳转。
 *  图标语义 Plus=「创建新 Bot」(唤起后仍可切换云端/本地,个人空间亦可达本地 Bot 创建)。 */
export function CreateBotEntryButton({
  spaces,
  currentSpaceId,
  onSelectSpace,
}: {
  spaces: Space[];
  currentSpaceId?: number;
  onSelectSpace(spaceId: number): void;
}) {
  const [open, setOpen] = useState(false);
  // 弹窗内选中项:打开时重置为当前空间(默认选中),点选下拉选项仅更新选中不触发跳转。
  const [selectedSpaceId, setSelectedSpaceId] = useState<number | undefined>(currentSpaceId);
  return (
    <Modal
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) setSelectedSpaceId(currentSpaceId);
      }}
    >
      <ModalTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label="创建新 Bot"
          className="h-6 w-6 rounded-md text-content-icon hover:text-primary"
          onClick={() => {
            Tracert.click(Points.workspaceChat.createBotEntry);
          }}
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
        </Button>
      </ModalTrigger>
      <ModalContent size="sm">
        <ModalHeader>
          <ModalTitle>创建云端 Bot</ModalTitle>
          <ModalDescription>将前往「Bot 管理」页面完成创建，请先选择 Bot 归属的空间。</ModalDescription>
        </ModalHeader>
        <div className="flex flex-col gap-1.5">
          <span className="text-xs font-normal text-content-soft">目标空间</span>
          {/* 空间下拉(2026-11-05 三轮验收:列表按钮改 Select,label「目标空间」):
              选项顺序由装配层按侧栏切换序排好;选中勾选由 SelectItem 自带指示承载;
              空列表禁用并以 placeholder 提示。 */}
          <Select
            value={selectedSpaceId !== undefined ? String(selectedSpaceId) : undefined}
            onValueChange={(value) => {
              const next = Number(value);
              setSelectedSpaceId(Number.isFinite(next) ? next : undefined);
            }}
          >
            <SelectTrigger aria-label="目标空间" disabled={spaces.length === 0}>
              <SelectValue placeholder={spaces.length === 0 ? '暂无可选空间' : '选择空间'} />
            </SelectTrigger>
            <SelectContent>
              {spaces.map((space) => (
                <SelectItem key={space.spaceId} value={String(space.spaceId)}>
                  <span className="flex items-center gap-2">
                    {space.spaceType === 'PERSONAL' ? (
                      <User className="h-3.5 w-3.5 shrink-0 text-brand" aria-hidden="true" />
                    ) : (
                      <Users className="h-3.5 w-3.5 shrink-0 text-brand" aria-hidden="true" />
                    )}
                    <span className="min-w-0 truncate">{space.spaceName}</span>
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <ModalFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            取消
          </Button>
          <Button
            disabled={selectedSpaceId === undefined}
            onClick={() => {
              setOpen(false);
              if (selectedSpaceId !== undefined) onSelectSpace(selectedSpaceId);
            }}
          >
            前往创建
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
