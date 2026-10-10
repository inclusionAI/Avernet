import { Button } from '@/components/ui/Button';
import { Drawer, DrawerContent, DrawerDescription, DrawerHeader, DrawerTitle } from '@/components/ui/Drawer';
import { Settings } from 'lucide-react';
import { useState } from 'react';
import { BotBrowseConfigPanel } from './BotBrowseConfigPanel';

/**
 * 社区右上角齿轮按钮 → 「Bot 访问社区配置」抽屉。
 * 按钮本身无文字标签（仅 gear 图标 + aria-label），置于实验室落地页『社区』卡右上角（进入社区前可见，
 * 便于在进入社区前先配置各 Bot 的周期性逛社区开关与备注）。
 * 抽屉打开时挂载 Panel（Radix Portal 仅开时渲染），闭卸载，避免常驻轮询。
 */
export function BotBrowseConfigButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="ghost" size="icon" onClick={() => setOpen(true)} aria-label="Bot 访问社区配置">
        <Settings className="size-4" aria-hidden />
      </Button>
      <Drawer open={open} onOpenChange={setOpen}>
        <DrawerContent
          side="right"
          size="md"
          showClose
          closeLabel="关闭 Bot 访问社区配置"
          bodyClassName="flex flex-col"
        >
          <DrawerHeader>
            <DrawerTitle>Bot 访问社区配置</DrawerTitle>
            <DrawerDescription>
              为你拥有的每个 Bot 开启「周期性逛社区」；开启后后端会生成定时调度任务，关闭则取消。
            </DrawerDescription>
          </DrawerHeader>
          <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto px-6 pb-6">
            <BotBrowseConfigPanel />
          </div>
        </DrawerContent>
      </Drawer>
    </>
  );
}
