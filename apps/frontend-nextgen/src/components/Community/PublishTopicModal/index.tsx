import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Segmented } from '@/components/ui/Segmented';
import { Textarea } from '@/components/ui/Textarea';
import type {
  CommunityAttachment,
  CommunityPollSelection,
  CommunityPublishTopicType,
  CreateCommunityTopicDraft,
} from '@/domain/community/types';
import {
  MAX_COMMUNITY_POLL_OPTIONS,
  MAX_COMMUNITY_SHARE_ATTACHMENTS,
} from '@/services/community/communityService';
import { ArrowLeft, CircleHelp, FileText, MessageSquare, Share2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { HelpEditor } from './HelpEditor';
import { PollEditor } from './PollEditor';
import { ShareAttachmentEditor } from './ShareAttachmentEditor';
import { attachmentValidationError, type AttachmentDraft, type DraftErrors } from './model';

interface PublishTopicModalProps {
  open: boolean;
  publishing: boolean;
  onOpenChange: (open: boolean) => void;
  onPublish: (draft: CreateCommunityTopicDraft) => Promise<boolean>;
  onUploadAttachment: (file: File) => Promise<CommunityAttachment>;
}

const topicTypeOptions = [
  { value: 'discussion' as const, label: '讨论', icon: <MessageSquare className="size-3.5" aria-hidden /> },
  { value: 'share' as const, label: '分享', icon: <Share2 className="size-3.5" aria-hidden /> },
  { value: 'poll' as const, label: '投票', icon: <FileText className="size-3.5" aria-hidden /> },
  { value: 'help' as const, label: '求助', icon: <CircleHelp className="size-3.5" aria-hidden /> },
];

/** 发布讨论、分享、投票和求助主题；分享附件在发布前完成真实上传。 */
export function PublishTopicModal({
  open,
  publishing,
  onOpenChange,
  onPublish,
  onUploadAttachment,
}: PublishTopicModalProps) {
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [topicType, setTopicType] = useState<CommunityPublishTopicType>('discussion');
  const [pollSelection, setPollSelection] = useState<CommunityPollSelection>('single');
  const [pollOptions, setPollOptions] = useState<string[]>(['', '']);
  const [pollDeadline, setPollDeadline] = useState('');
  const [helpExpectedTime, setHelpExpectedTime] = useState('');
  const [attachments, setAttachments] = useState<AttachmentDraft[]>([]);
  const [errors, setErrors] = useState<DraftErrors>({});
  const attachmentKeyRef = useRef(0);

  const resetDraft = () => {
    setTitle('');
    setBody('');
    setTopicType('discussion');
    setPollSelection('single');
    setPollOptions(['', '']);
    setPollDeadline('');
    setHelpExpectedTime('');
    setAttachments([]);
    setErrors({});
  };

  useEffect(() => {
    if (!open) resetDraft();
  }, [open]);

  const clearError = (key: keyof DraftErrors) => {
    setErrors((current) => ({ ...current, [key]: undefined }));
  };

  const uploadAttachment = async (draft: AttachmentDraft) => {
    setAttachments((current) =>
      current.map((item) =>
        item.key === draft.key ? { ...item, status: 'uploading', error: undefined } : item,
      ),
    );
    try {
      const attachment = await onUploadAttachment(draft.file);
      setAttachments((current) =>
        current.map((item) =>
          item.key === draft.key ? { ...item, status: 'uploaded', attachment, error: undefined } : item,
        ),
      );
      clearError('shareAttachments');
    } catch (error) {
      const message = error instanceof Error ? error.message : '附件上传失败';
      setAttachments((current) =>
        current.map((item) => (item.key === draft.key ? { ...item, status: 'error', error: message } : item)),
      );
    }
  };

  const addFiles = (files: File[]) => {
    if (!files.length) return;
    if (attachments.length + files.length > MAX_COMMUNITY_SHARE_ATTACHMENTS) {
      setErrors((current) => ({
        ...current,
        shareAttachments: `每个分享最多上传 ${MAX_COMMUNITY_SHARE_ATTACHMENTS} 个附件`,
      }));
      return;
    }
    const drafts = files.map((file) => {
      attachmentKeyRef.current += 1;
      const validationError = attachmentValidationError(file);
      return {
        key: `attachment-${attachmentKeyRef.current}`,
        file,
        status: validationError ? ('error' as const) : ('uploading' as const),
        error: validationError,
      };
    });
    setAttachments((current) => [...current, ...drafts]);
    clearError('shareAttachments');
    drafts.filter((draft) => !draft.error).forEach((draft) => void uploadAttachment(draft));
  };

  const handleSubmit = async () => {
    const nextErrors: DraftErrors = {
      ...(!title.trim() ? { title: '请输入主题标题' } : {}),
      ...(!body.trim() ? { body: '请输入主题正文' } : {}),
    };
    const options = pollOptions.map((option) => option.trim()).filter(Boolean);
    if (topicType === 'poll') {
      if (options.length < 2 || options.length > MAX_COMMUNITY_POLL_OPTIONS) {
        nextErrors.pollOptions =
          options.length < 2
            ? '投票主题至少需要两个有效选项'
            : `投票选项最多 ${MAX_COMMUNITY_POLL_OPTIONS} 个`;
      }
      if (new Set(options).size !== options.length) nextErrors.pollOptions = '投票选项不能重复';
      if (pollDeadline && Number.isNaN(new Date(pollDeadline).getTime())) {
        nextErrors.pollDeadline = '请输入有效的截止时间';
      }
    }

    const uploadedAttachments = attachments.flatMap((item) =>
      item.status === 'uploaded' && item.attachment ? [item.attachment] : [],
    );
    if (topicType === 'share' && uploadedAttachments.length === 0) {
      nextErrors.shareAttachments = '分享主题至少需要一个上传成功的附件';
    }
    if (topicType === 'share' && attachments.some((item) => item.status !== 'uploaded')) {
      nextErrors.shareAttachments = '请等待附件上传完成，并处理上传失败的文件';
    }
    if (topicType === 'help' && helpExpectedTime.trim().length > 128) {
      nextErrors.helpExpectedTime = '期望解决时间不能超过 128 个字符';
    }

    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) return;
    const draft: CreateCommunityTopicDraft = {
      title,
      body,
      topicType,
      ...(topicType === 'poll'
        ? {
            poll: {
              selection: pollSelection,
              options,
              ...(pollDeadline ? { deadlineAt: new Date(pollDeadline).toISOString() } : {}),
            },
          }
        : {}),
      ...(topicType === 'share'
        ? { share: { attachmentIds: uploadedAttachments.map((attachment) => attachment.id) } }
        : {}),
      ...(topicType === 'help'
        ? { help: { ...(helpExpectedTime.trim() ? { expectedTime: helpExpectedTime.trim() } : {}) } }
        : {}),
    };
    if (await onPublish(draft)) onOpenChange(false);
  };

  if (!open) return null;
  return (
    <section className="app-scrollbar flex h-full flex-col overflow-y-auto" aria-label="发布主题编辑区">
      <header className="sticky top-0 z-10 flex items-center gap-2 border-b border-border bg-background/95 px-6 py-4 backdrop-blur">
        <Button variant="ghost" size="icon" onClick={() => onOpenChange(false)} aria-label="返回社区列表">
          <ArrowLeft className="size-4" aria-hidden />
        </Button>
        <h2 className="m-0 text-lg font-semibold text-foreground">发布主题</h2>
        <div className="ml-auto flex items-center gap-3">
          <Button variant="outline" size="sm" className="px-4 text-xs" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            size="sm"
            className="px-4 text-xs"
            loading={publishing}
            onClick={() => void handleSubmit()}
          >
            发布
          </Button>
        </div>
      </header>

      <div className="flex flex-1 flex-col gap-4 px-6 py-6">
        <Segmented<CommunityPublishTopicType>
          value={topicType}
          onChange={setTopicType}
          aria-label="主题类型"
          options={topicTypeOptions}
          className="h-10 w-full max-w-md"
        />
        <Input
          className="h-11 rounded-lg border-border bg-background px-3 text-base font-semibold"
          value={title}
          onChange={(event) => {
            setTitle(event.target.value);
            clearError('title');
          }}
          placeholder="主题"
          maxLength={80}
          aria-label="主题标题"
          aria-invalid={!!errors.title}
        />
        {errors.title ? <p className="m-0 text-xs text-destructive">{errors.title}</p> : null}
        <Textarea
          className="min-h-[260px] rounded-lg border-border bg-background px-3 py-4 text-base leading-relaxed"
          value={body}
          onChange={(event) => {
            setBody(event.target.value);
            clearError('body');
          }}
          placeholder="输入正文内容..."
          maxLength={4000}
          variant={errors.body ? 'error' : 'default'}
          aria-label="正文"
          aria-invalid={!!errors.body}
        />
        {errors.body ? <p className="m-0 text-xs text-destructive">{errors.body}</p> : null}

        {topicType === 'share' ? (
          <ShareAttachmentEditor
            attachments={attachments}
            error={errors.shareAttachments}
            onAddFiles={addFiles}
            onRetry={(draft) => void uploadAttachment(draft)}
            onRemove={(key) => setAttachments((current) => current.filter((item) => item.key !== key))}
          />
        ) : null}
        {topicType === 'poll' ? (
          <PollEditor
            selection={pollSelection}
            options={pollOptions}
            deadline={pollDeadline}
            optionsError={errors.pollOptions}
            deadlineError={errors.pollDeadline}
            onSelectionChange={setPollSelection}
            onOptionsChange={setPollOptions}
            onDeadlineChange={setPollDeadline}
            onClearOptionsError={() => clearError('pollOptions')}
            onClearDeadlineError={() => clearError('pollDeadline')}
          />
        ) : null}
        {topicType === 'help' ? (
          <HelpEditor
            expectedTime={helpExpectedTime}
            error={errors.helpExpectedTime}
            onChange={(value) => {
              setHelpExpectedTime(value);
              clearError('helpExpectedTime');
            }}
          />
        ) : null}
      </div>
    </section>
  );
}
