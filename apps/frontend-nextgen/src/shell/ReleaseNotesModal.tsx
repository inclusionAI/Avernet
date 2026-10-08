// 版本发布说明 Modal（Open Core 纯 UI）。展示雨燕配置拉取的用户须知（红主题）+ 新版本发布（蓝主题）。
// 富文本 HTML 来自受信内部运营平台（雨燕配置），用 dangerouslySetInnerHTML 渲染；不引入新 HTML 解析依赖（体积）。
// 数据由 useReleaseNotes 提供（Open Core capability=null → 此 Modal 不被渲染）。
// 禁 antd/裸 button，用项目 <Modal>/<Button>。≤150 行。
import type { ReleaseNotesData } from '@/capabilities';
import { Button, Modal, ModalContent } from '@/components/ui';
import { X } from 'lucide-react';
import { Fragment } from 'react';

interface ReleaseNotesModalProps {
  open: boolean;
  data: ReleaseNotesData | null;
  onClose: () => void;
}

function EmptyContent() {
  return <p className="italic text-sm text-muted-foreground">暂无内容</p>;
}

/** 内嵌富文本（受信源 + 基础 prose 样式）。 */
function RichText({ html }: { html?: string }) {
  if (!html) return <EmptyContent />;
  return (
    <div
      className="prose prose-sm max-w-none break-words text-foreground [&_ol]:pl-4 [&_ul]:pl-4"
      // eslint-disable-next-line react/no-danger -- 受信源：雨燕配置平台运营内容
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

export function ReleaseNotesModal({ open, data, onClose }: ReleaseNotesModalProps) {
  return (
    <Modal open={open} onOpenChange={(v) => !v && onClose()}>
      <ModalContent size="xl" showClose={false} className="flex max-h-[85vh] flex-col gap-0 p-0">
        {/* 标题栏 */}
        <div className="flex flex-shrink-0 items-center justify-between border-b border-border px-6 py-4">
          <h2 className="text-base font-medium">版本发布说明</h2>
          <Button variant="ghost" size="sm" leftIcon={<X className="h-4 w-4" />} onClick={onClose}>
            关闭
          </Button>
        </div>
        {/* 内容区 */}
        <div className="flex-1 space-y-4 overflow-y-auto bg-muted/30 p-5">
          {/* 用户须知（红主题） */}
          <div className="overflow-hidden rounded-lg border border-red-200 bg-red-50">
            <div className="flex items-center gap-2 bg-red-100/50 px-4 py-2.5">
              <span className="h-2 w-2 rounded-full bg-red-500" />
              <span className="text-sm font-medium text-red-600">用户须知</span>
            </div>
            <div className="rounded-lg border border-red-100 bg-white px-4 py-3">
              <RichText html={data?.userReadmeHtml} />
            </div>
          </div>
          {/* 新版本发布（蓝主题） */}
          <div className="overflow-hidden rounded-lg border border-blue-200 bg-blue-50">
            <div className="flex items-center justify-between bg-blue-100/50 px-4 py-2.5">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-blue-500" />
                <span className="text-sm font-medium text-blue-600">新版本发布</span>
              </div>
              {data?.version && (
                <span className="text-xs text-blue-500">
                  v{data.version}
                  {data?.date ? <Fragment> · {data.date}</Fragment> : null}
                </span>
              )}
            </div>
            <div className="rounded-lg border border-blue-100 bg-white px-4 py-3">
              <RichText html={data?.releaseNoteHtml} />
            </div>
          </div>
        </div>
      </ModalContent>
    </Modal>
  );
}

export default ReleaseNotesModal;
