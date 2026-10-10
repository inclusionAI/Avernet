import { Avatar } from '@/components/ui/Avatar';
import { Badge } from '@/components/ui/Badge';
import { Drawer, DrawerContent, DrawerDescription, DrawerHeader, DrawerTitle } from '@/components/ui/Drawer';
import type { BotDomain } from '@/services/botWorkshop';
import { lifecycleLabel } from './BotCard/config';

function InfoItem({ label, value }: { label: string; value?: string }) {
  return (
    <div className="grid grid-cols-[7rem_minmax(0,1fr)] gap-3 border-b border-border py-3 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words text-foreground">{value || '—'}</dd>
    </div>
  );
}

export function BotBasicInfoDrawer({ bot, onClose }: { bot?: BotDomain; onClose: () => void }) {
  return (
    <Drawer open={Boolean(bot)} onOpenChange={(open) => !open && onClose()}>
      <DrawerContent size="md">
        {bot ? (
          <>
            <DrawerHeader>
              <div className="flex items-center gap-3">
                <Avatar name={bot.name} src={bot.avatarUrl} size={48} />
                <div className="min-w-0">
                  <DrawerTitle className="truncate text-lg font-semibold">{bot.name}</DrawerTitle>
                  <DrawerDescription className="mt-1">基础信息（当前账号无权进入 Bot 配置页）</DrawerDescription>
                </div>
              </div>
            </DrawerHeader>
            <div className="mt-4">
              <dl>
                <InfoItem label="Bot ID" value={bot.id} />
                <InfoItem label="描述" value={bot.description} />
                <InfoItem label="Owner" value={bot.ownerName || bot.ownerId} />
                <InfoItem label="归属空间" value={bot.spaceName || bot.spaceId} />
                <InfoItem label="引擎" value={bot.runtime.engine} />
              </dl>
              <div className="flex items-center gap-3 py-3 text-sm">
                <span className="w-28 text-muted-foreground">状态</span>
                <Badge
                  tone={bot.lifecycle === 'failed' ? 'error' : bot.lifecycle === 'running' ? 'success' : 'neutral'}
                >
                  {lifecycleLabel[bot.lifecycle]}
                </Badge>
              </div>
            </div>
          </>
        ) : null}
      </DrawerContent>
    </Drawer>
  );
}
