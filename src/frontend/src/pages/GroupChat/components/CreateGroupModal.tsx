/**
 * CreateGroupModal - 创建群组弹窗
 *
 * 用于在群聊列表页快速创建群组
 *
 * 改造说明：
 * - 增加好友/可见性校验
 * - 展示 protected Bot 的好友状态提示
 * - 内部调用 useGroups hook 获取可用 Bot 列表
 * - 支持名称搜索和智能搜索两种模式
 * - 支持根据协作室名称智能推荐 Bot
 */

import { useExt } from '@/capabilities';
import { Button } from '@/components';
import BotAvatar from '@/components/BotAvatar';
import GoldBadge from '@/components/GoldBadge';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { useActor } from '@/hooks/useActor';
import type { ActorBot } from '@/services/backend-api/ActorController';
import type { DynamicStatus } from '@/services/backend-api/BcnController';
import * as BcnController from '@/services/backend-api/BcnController';
import { AppExt } from '@/shell';
import { computeIsOnline, useBotNetworkStore } from '@/stores/botNetworkStore';
import { BotRecommendTracker } from '@/utils/botRecommendTracker';
import { cn } from '@/utils/utils';
import {
  HighlightStyle,
  StreamLanguage,
  syntaxHighlighting,
} from '@codemirror/language';
import { EditorView } from '@codemirror/view';
import { tags as highlightTags } from '@lezer/highlight';
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  EyeOff,
  Globe,
  Loader2,
  Lock,
  MessagesSquare,
  Network,
  Plus,
  Search,
  Sparkles,
  Workflow,
  X,
} from 'lucide-react';
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  MANAGER_WORKER_DEFAULT_WORKER_TIP,
  MANAGER_WORKER_ENGINE_SUPPORT_TIP,
} from '../constants';
import { useCollaborationYamlValidation } from '../hooks/useCollaborationYamlValidation';
import { useGroups } from '../hooks/useGroups';
import type {
  CreateGroupParams,
  GroupInfo,
  GroupMemberRole,
  GroupStrategy,
} from '../types';
import { shouldUseCompactCollaborationFlow } from '../utils/collaborationFlowLayout';
import {
  buildCollaborationBindingViews,
  type CollaborationBindingView,
} from '../utils/collaborationGraphLayout';
import {
  buildCollaborationParticipantDefinitions,
  formatCollaborationValidationErrors,
  getCollaborationParticipantLabel,
  type CollaborationParticipantDefinition,
} from '../utils/collaborationValidation';
import CollaborationFlowPreviewLoader from './CollaborationFlowPreviewLoader';
import CollaborationFlowWorkspace from './CollaborationFlowWorkspace';
import PrivateBotHint from './PrivateBotHint';

/** 最大可选成员 Bot 数量 */
const MAX_MEMBER_BOTS = 19;

/** 一层 Tab 类型：我的好友 / 可协作Bot */
type MemberTab = 'friends' | 'collaborate';

/** 二层 Tab 类型（仅在可协作Bot下显示）：按名称筛选 / 按画像筛选 */
type FilterMode = 'name' | 'profile';

type CollaborationAuthoringStage = 'yaml' | 'bindings';

type InviteCandidateStatus = {
  dynamic_status?: DynamicStatus;
  is_friend?: boolean | null;
  is_online?: boolean;
};

const COMMUNICATION_WARNING_TEXT =
  'Bot 与协作网络连接异常，请稍后重试或检查 Bot 状态';

const LazyCodeMirror = React.lazy(() => import('@uiw/react-codemirror'));

type YamlParserState = {
  blockScalarIndent: number | null;
};

const yamlLanguage = StreamLanguage.define<YamlParserState>({
  name: 'yaml',
  startState: () => ({ blockScalarIndent: null }),
  token(stream, state) {
    if (stream.sol()) {
      const isBlankLine = /^\s*$/.test(stream.string);
      if (
        state.blockScalarIndent !== null &&
        (isBlankLine || stream.indentation() > state.blockScalarIndent)
      ) {
        stream.skipToEnd();
        return 'string';
      }
      if (state.blockScalarIndent !== null) {
        state.blockScalarIndent = null;
      }
      if (stream.match(/^\s*(?:---|\.\.\.)\s*(?:#.*)?$/)) {
        return 'meta';
      }
    }

    if (stream.eatSpace()) return null;
    if (stream.peek() === '#') {
      stream.skipToEnd();
      return 'comment';
    }
    if (stream.match(/^-\s*/)) return 'punctuation';
    if (
      stream.match(
        /^(?:"(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^\s:#][^:#]*?)(?=\s*:)/,
      )
    ) {
      return 'propertyName';
    }
    if (stream.match(/^:\s*/)) return 'punctuation';
    if (stream.match(/^[|>][+-]?/)) {
      state.blockScalarIndent = stream.indentation();
      return 'operator';
    }
    if (stream.match(/^"(?:[^"\\]|\\.)*"?/)) return 'string';
    if (stream.match(/^'(?:[^']|'')*'?/)) return 'string';
    if (stream.match(/^(?:true|false|yes|no|on|off)\b/i)) return 'bool';
    if (stream.match(/^(?:null|~)\b/i)) return 'null';
    if (
      stream.match(
        /^[-+]?(?:0x[\da-f]+|0o[0-7]+|(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)\b/i,
      )
    ) {
      return 'number';
    }
    if (stream.match(/^[{}[\],]/)) return 'punctuation';
    if (stream.match(/^[*&][\w-]+/)) return 'variableName';
    if (stream.match(/^![\w:/.-]+/)) return 'keyword';
    if (stream.match(/^[^\s#,[\]{}]+/)) return 'string';

    stream.next();
    return null;
  },
  languageData: {
    commentTokens: { line: '#' },
  },
});

const yamlHighlightStyle = HighlightStyle.define([
  { tag: highlightTags.propertyName, color: '#7c3aed', fontWeight: '600' },
  { tag: highlightTags.string, color: '#0f766e' },
  { tag: highlightTags.number, color: '#2563eb' },
  { tag: [highlightTags.bool, highlightTags.null], color: '#b45309' },
  { tag: highlightTags.keyword, color: '#be123c' },
  { tag: highlightTags.variableName, color: '#4f46e5' },
  { tag: highlightTags.comment, color: '#94a3b8', fontStyle: 'italic' },
  { tag: highlightTags.meta, color: '#64748b' },
  {
    tag: [highlightTags.punctuation, highlightTags.operator],
    color: '#64748b',
  },
]);

const yamlEditorExtensions = [
  yamlLanguage,
  syntaxHighlighting(yamlHighlightStyle),
  EditorView.lineWrapping,
];

const YAML_EDITOR_PLACEHOLDER =
  'name: 自定义协作\nparticipants:\n  assistant:\n    display_name: 助手\n    required: true\nruntime:\n  kind: state_machine\n  state_machine:\n    nodes:\n      answer:\n        kind: bot_task\n        display_name: 输出结果\n        assignee:\n          type: bot_binding\n          binding: assistant\n        instruction: 请根据用户输入输出最终结果。\n        final_output: true';

const FREE_COLLABORATION_TEMPLATE_ID = '__free__';

const COLLABORATION_TAG_COLOR_CLASS: Record<string, string> = {
  judge: 'bg-rose-50 text-rose-600 border-rose-100',
  branching: 'bg-amber-50 text-amber-700 border-amber-100',
  parallel: 'bg-blue-50 text-blue-700 border-blue-100',
  serial: 'bg-cyan-50 text-cyan-700 border-cyan-100',
  'single-step': 'bg-teal-50 text-teal-700 border-teal-100',
};

const formatAutoGroupLabel = (names: string[], fallback: string) => {
  const validNames = names.map((name) => name.trim()).filter(Boolean);
  if (!validNames.length) return fallback;
  return validNames.length > 5
    ? `${validNames.slice(0, 5).join('、')}等`
    : validNames.join('、');
};

interface CreateGroupModalProps {
  open: boolean;
  onClose: () => void;
  onSuccess: (group: GroupInfo) => void;
  /** 当前视角的 Actor 类型 */
  actorKind?: 'human' | 'bot';
}

const CreateGroupModal: React.FC<CreateGroupModalProps> = ({
  open,
  onClose,
  onSuccess,
  actorKind = 'bot',
}) => {
  // 协作模板「查看文档」链接走 resources 契约：开源默认 null 不渲染
  const { bcnCollaborationDocUrl } = useExt(AppExt).resources;
  const [topic, setTopic] = useState('');
  const [description, setDescription] = useState('');
  const [consultantBots, setConsultantBots] = useState<string[]>([]);
  const [botSearch, setBotSearch] = useState('');
  const [error, setError] = useState<string>('');
  const [autoReply, setAutoReply] = useState(true); // 是否开启自动回复
  const [groupStrategy, setGroupStrategy] = useState<GroupStrategy>('chat');
  const [collaborationYamlTemplate, setCollaborationYamlTemplate] =
    useState('');
  const [collaborationTemplates, setCollaborationTemplates] =
    useState<BcnController.CollaborationTemplatesResponse | null>(null);
  const [hasLoadedCollaborationTemplates, setHasLoadedCollaborationTemplates] =
    useState(false);
  const [isLoadingCollaborationTemplates, setIsLoadingCollaborationTemplates] =
    useState(false);
  const [isLoadingCollaborationYaml, setIsLoadingCollaborationYaml] =
    useState(false);
  const [
    collaborationTemplateDropdownOpen,
    setCollaborationTemplateDropdownOpen,
  ] = useState(false);
  const [collaborationDefinitionYaml, setCollaborationDefinitionYaml] =
    useState('');
  const [validatedCollaborationYaml, setValidatedCollaborationYaml] =
    useState('');
  const [validatedCollaborationGraph, setValidatedCollaborationGraph] =
    useState<BcnController.CollaborationDefinitionGraphPreview | null>(null);
  const [validatedCollaborationSummary, setValidatedCollaborationSummary] =
    useState<BcnController.CollaborationDefinitionValidationSummary | null>(
      null,
    );
  const [collaborationAuthoringStage, setCollaborationAuthoringStage] =
    useState<CollaborationAuthoringStage>('yaml');
  const [selectedCollaborationNodeId, setSelectedCollaborationNodeId] =
    useState<string>();
  const [isCompactCollaborationFlowOpen, setIsCompactCollaborationFlowOpen] =
    useState(false);
  const [
    isCompactCollaborationFlowLayout,
    setIsCompactCollaborationFlowLayout,
  ] = useState(
    () =>
      typeof window === 'undefined' ||
      shouldUseCompactCollaborationFlow(window.innerWidth),
  );
  const [participantDefinitions, setParticipantDefinitions] = useState<
    CollaborationParticipantDefinition[]
  >([]);
  const [activeParticipantKey, setActiveParticipantKey] = useState('');
  const [participantBindings, setParticipantBindings] = useState<
    Record<string, string[]>
  >({});
  const [canScrollParticipantTabsLeft, setCanScrollParticipantTabsLeft] =
    useState(false);
  const [canScrollParticipantTabsRight, setCanScrollParticipantTabsRight] =
    useState(false);
  const [masterBot, setMasterBot] = useState<string>('');
  const [masterBotDropdownOpen, setMasterBotDropdownOpen] = useState(false);
  const masterBotDropdownRef = useRef<HTMLDivElement>(null);
  const collaborationTemplateDropdownRef = useRef<HTMLDivElement>(null);
  // 自由聊天型：群主 Bot 选择
  const [driverBotId, setDriverBotId] = useState<string>('');
  const [driverBotDropdownOpen, setDriverBotDropdownOpen] = useState(false);
  const driverBotDropdownRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const selectedBotCacheRef = useRef<Map<string, any>>(new Map());
  const collaborationTemplatesRequestIdRef = useRef(0);
  const collaborationYamlRequestIdRef = useRef(0);
  const collaborationYamlTemplateRef = useRef('');
  const collaborationDefinitionYamlRef = useRef('');
  const participantTabsRef = useRef<HTMLDivElement>(null);
  const participantTabButtonRefs = useRef(new Map<string, HTMLButtonElement>());

  useEffect(() => {
    const updateCollaborationFlowLayout = () => {
      const compact = shouldUseCompactCollaborationFlow(window.innerWidth);
      setIsCompactCollaborationFlowLayout(compact);
      if (!compact) setIsCompactCollaborationFlowOpen(false);
    };

    updateCollaborationFlowLayout();
    window.addEventListener('resize', updateCollaborationFlowLayout);
    return () =>
      window.removeEventListener('resize', updateCollaborationFlowLayout);
  }, []);

  // 埋点 tracker - 弹窗打开时创建，关闭后失效
  const trackerRef = useRef<BotRecommendTracker | null>(null);
  // 请求开始时间（用于计算 latency_ms）
  const requestStartTimeRef = useRef<number>(0);
  // 当前搜索结果的 trace_id
  const currentTraceIdRef = useRef<string | null>(null);
  // 当前搜索结果的 bot 列表（用于查找 position）
  const currentBotsRef = useRef<{ bot_uuid: string; position: number }[]>([]);

  // 一层 Tab：我的好友 / 可协作Bot
  const [memberTab, setMemberTab] = useState<MemberTab>('friends');
  // 二层 Tab（仅在可协作Bot下显示）：按名称筛选 / 按画像筛选
  const [filterMode, setFilterMode] = useState<FilterMode>('name');
  // 名称搜索结果（API 搜索）
  const [nameSearchBots, setNameSearchBots] = useState<
    Array<{
      bot_uuid: string;
      bot_name: string;
      summary?: string;
      avatar_url?: string;
      visibility?: 'public' | 'protected';
      is_friend?: boolean | null;
      short_profile?: string;
      dynamic_status?: { status: string };
      is_online?: boolean;
      tags?: { trust_level?: string };
    }>
  >([]);
  const [isLoadingNameSearch, setIsLoadingNameSearch] = useState(false);
  // 智能搜索结果
  const [smartSearchBots, setSmartSearchBots] = useState<
    Array<{
      bot_uuid: string;
      bot_name: string;
      summary?: string;
      avatar_url?: string;
      visibility?: 'public' | 'protected';
      is_friend?: boolean | null;
      short_profile?: string;
      tags?: {
        trust_level?: string;
      };
      dynamic_status?: { status: string };
      is_online?: boolean;
    }>
  >([]);
  const [isLoadingSmartSearch, setIsLoadingSmartSearch] = useState(false);
  // 智能推荐加载状态
  const [isRecommending, setIsRecommending] = useState(false);
  // 我的好友列表
  const [friendBots, setFriendBots] = useState<
    Array<{
      bot_uuid: string;
      bot_name: string;
      summary?: string;
      avatar_url?: string;
      visibility?: 'public' | 'protected';
      is_friend?: boolean | null;
      dynamic_status?: { status: string };
      is_online?: boolean;
    }>
  >([]);
  const [isLoadingFriends, setIsLoadingFriends] = useState(false);

  // 从 store 获取当前 Tab Bot（发起者）
  const originator = useBotNetworkStore((state) => state.driverBot);

  // Actor hook（用于替换 discoverBots）
  const { loadActors, searchActors } = useActor();
  const {
    validateCollaborationYaml,
    cancelCollaborationYamlValidation,
    isValidatingCollaborationYaml,
  } = useCollaborationYamlValidation();

  const originatorStatus =
    originator?.status ??
    (originator?.is_online === undefined
      ? undefined
      : originator.is_online
      ? 'online'
      : 'hidden');
  const isOriginatorHidden = originatorStatus === 'hidden';
  const isOriginatorUnreachable =
    !isOriginatorHidden &&
    originatorStatus === 'online' &&
    originator?.dynamic_status?.status === 'offline';
  const isOriginatorOnline = originatorStatus === 'online';
  const canCreateStateMachineGroup = actorKind !== 'human';
  const collaborationTemplateList = useMemo(
    () =>
      [...(collaborationTemplates?.templates || [])].sort(
        (a, b) => (a.priority ?? 0) - (b.priority ?? 0),
      ),
    [collaborationTemplates],
  );
  const collaborationTemplateLanguage =
    collaborationTemplates?.default_language || 'zh-CN';
  const selectedCollaborationTemplate = useMemo(
    () =>
      collaborationTemplateList.find(
        (template) => template.id === collaborationYamlTemplate,
      ),
    [collaborationTemplateList, collaborationYamlTemplate],
  );
  const isFreeCollaborationTemplateSelected =
    collaborationYamlTemplate === FREE_COLLABORATION_TEMPLATE_ID;
  const isCollaborationTemplateMode = !isFreeCollaborationTemplateSelected;
  const isCollaborationTemplateLoading =
    isLoadingCollaborationYaml ||
    (isLoadingCollaborationTemplates && !isFreeCollaborationTemplateSelected);
  const getCollaborationTagLabel = useCallback(
    (tag: string) => {
      const labels = collaborationTemplates?.tag_labels?.[tag];
      return (
        labels?.[collaborationTemplateLanguage] ||
        labels?.['zh-CN'] ||
        labels?.['en-US'] ||
        tag
      );
    },
    [collaborationTemplateLanguage, collaborationTemplates],
  );
  const getCollaborationTagColorClass = useCallback(
    (tag: string) =>
      COLLABORATION_TAG_COLOR_CLASS[tag] ||
      'bg-slate-100 text-slate-600 border-slate-200',
    [],
  );

  useEffect(() => {
    collaborationYamlTemplateRef.current = collaborationYamlTemplate;
  }, [collaborationYamlTemplate]);

  useEffect(() => {
    collaborationDefinitionYamlRef.current = collaborationDefinitionYaml;
  }, [collaborationDefinitionYaml]);

  const isCollaborationYamlValidated =
    !!validatedCollaborationYaml &&
    validatedCollaborationYaml === collaborationDefinitionYaml &&
    participantDefinitions.length > 0;
  const activeBindingBotIds = activeParticipantKey
    ? (participantBindings[activeParticipantKey] || []).slice(0, 1)
    : [];
  const allBoundBotIds = useMemo(
    () =>
      Array.from(
        new Set(
          Object.values(participantBindings)
            .map((botIds) => botIds[0])
            .filter((botId): botId is string => Boolean(botId)),
        ),
      ),
    [participantBindings],
  );
  const boundParticipantCount = useMemo(
    () =>
      participantDefinitions.filter(
        (definition) => (participantBindings[definition.key] || []).length > 0,
      ).length,
    [participantBindings, participantDefinitions],
  );
  const isOriginatorBoundToParticipant =
    !!originator?.bot_uuid && allBoundBotIds.includes(originator.bot_uuid);
  const missingParticipantBindingLabels = useMemo(
    () =>
      participantDefinitions
        .filter(
          (definition) =>
            (definition.required || definition.assigned) &&
            !(participantBindings[definition.key] || []).length,
        )
        .map(getCollaborationParticipantLabel),
    [participantDefinitions, participantBindings],
  );
  const updateParticipantTabsScrollState = useCallback(() => {
    const container = participantTabsRef.current;
    if (!container) {
      setCanScrollParticipantTabsLeft(false);
      setCanScrollParticipantTabsRight(false);
      return;
    }

    const scrollEnd = container.scrollLeft + container.clientWidth;
    setCanScrollParticipantTabsLeft(container.scrollLeft > 1);
    setCanScrollParticipantTabsRight(scrollEnd < container.scrollWidth - 1);
  }, []);
  const scrollParticipantTabs = useCallback(
    (direction: 'previous' | 'next') => {
      const container = participantTabsRef.current;
      if (!container) return;

      const pageWidth = Math.max(container.clientWidth - 176, 176);
      container.scrollBy({
        left: direction === 'previous' ? -pageWidth : pageWidth,
        behavior: 'smooth',
      });
    },
    [],
  );

  useEffect(() => {
    const frameId = window.requestAnimationFrame(
      updateParticipantTabsScrollState,
    );
    window.addEventListener('resize', updateParticipantTabsScrollState);
    return () => {
      window.cancelAnimationFrame(frameId);
      window.removeEventListener('resize', updateParticipantTabsScrollState);
    };
  }, [participantDefinitions.length, updateParticipantTabsScrollState]);

  useEffect(() => {
    if (!activeParticipantKey) return;

    const frameId = window.requestAnimationFrame(() => {
      const container = participantTabsRef.current;
      const activeButton =
        participantTabButtonRefs.current.get(activeParticipantKey);
      if (!container || !activeButton) return;

      const buttonStart = activeButton.offsetLeft;
      const buttonEnd = buttonStart + activeButton.offsetWidth;
      const visibleStart = container.scrollLeft;
      const visibleEnd = visibleStart + container.clientWidth;

      if (buttonStart < visibleStart) {
        container.scrollTo({
          left: buttonStart,
          behavior: 'smooth',
        });
      } else if (buttonEnd > visibleEnd) {
        container.scrollTo({
          left: buttonEnd - container.clientWidth,
          behavior: 'smooth',
        });
      }
    });

    return () => window.cancelAnimationFrame(frameId);
  }, [activeParticipantKey, participantDefinitions.length]);

  // 内部调用 hook 获取群组相关数据和方法
  const {
    availableBots,
    isLoadingAvailableBots,
    hasMoreAvailableBots,
    isLoadingMoreBots,
    isCreatingGroup,
    loadAvailableBots,
    loadMoreAvailableBots,
    createGroup,
  } = useGroups();

  // 弹窗打开时：创建新的 tracker（生成新的 session_id）
  // 弹窗关闭时：tracker 失效（下次打开会创建新的）
  useEffect(() => {
    if (open) {
      // 弹窗打开时创建新的 tracker，生成新的 session_id
      trackerRef.current = new BotRecommendTracker();
      console.log(
        '[CreateGroupModal] New session created:',
        trackerRef.current?.getSessionId(),
      );
      // 初始化群主 Bot：bot 模式默认选自己，human 模式默认空（需手动选）
      if (actorKind === 'bot' && originator?.bot_uuid) {
        setDriverBotId(originator.bot_uuid);
      } else {
        setDriverBotId('');
      }
    }
  }, [open]);

  // 当群主 Bot 被从成员列表中移除时，自动回退
  useEffect(() => {
    if (
      groupStrategy === 'chat' &&
      driverBotId &&
      driverBotId !== originator?.bot_uuid &&
      !consultantBots.includes(driverBotId)
    ) {
      // bot 模式回退到自己，human 模式回退到空
      setDriverBotId(actorKind === 'bot' ? originator?.bot_uuid || '' : '');
    }
  }, [
    consultantBots,
    driverBotId,
    originator?.bot_uuid,
    groupStrategy,
    actorKind,
  ]);

  // 弹窗打开时加载第一页（隐身模式下不加载，human actor 跳过在线检查）
  useEffect(() => {
    if (!open || !originator?.bot_uuid) return;
    if (
      actorKind === 'human' ||
      (originator.is_online && originator.dynamic_status?.status !== 'offline')
    ) {
      loadAvailableBots(originator.bot_uuid);
    }
  }, [
    open,
    originator?.bot_uuid,
    originator?.is_online,
    originator?.dynamic_status?.status,
    actorKind,
    loadAvailableBots,
  ]);

  // 需要从成员选择中排除的 Bot（当前 Tab Bot 始终排除，由发起者自动加入逻辑处理）
  const excludedBotUuids = useMemo(() => {
    const excluded = new Set<string>();
    if (originator?.bot_uuid) excluded.add(originator.bot_uuid);
    return excluded;
  }, [originator?.bot_uuid]);

  // 可选 Bot（排除群主 Bot）- 用于名称搜索无搜索词时的初始列表
  const selectableBots = useMemo(
    () => availableBots.filter((bot) => !excludedBotUuids.has(bot.bot_uuid)),
    [availableBots, excludedBotUuids],
  );

  // 名称搜索结果（排除群主 Bot）
  const nameFilteredBots = useMemo(() => {
    return nameSearchBots.filter((bot) => !excludedBotUuids.has(bot.bot_uuid));
  }, [nameSearchBots, excludedBotUuids]);

  // 智能搜索：服务端搜索结果（排除群主 Bot）
  const smartFilteredBots = useMemo(() => {
    return smartSearchBots.filter((bot) => !excludedBotUuids.has(bot.bot_uuid));
  }, [smartSearchBots, excludedBotUuids]);

  // 当前显示的 Bot 列表（根据一层/二层 Tab）
  // 我的好友：本地过滤好友列表
  // 可协作Bot + 按名称筛选：有搜索词用 API 结果，无搜索词用初始列表
  // 可协作Bot + 按画像筛选：有搜索词用 API 结果，无搜索词用全量数据
  const filteredBots =
    memberTab === 'friends'
      ? friendBots.filter(
          (b) =>
            !botSearch?.trim() ||
            b.bot_name?.toLowerCase().includes(botSearch?.toLowerCase()),
        )
      : filterMode === 'name'
      ? botSearch.trim()
        ? nameFilteredBots
        : selectableBots
      : botSearch.trim()
      ? smartFilteredBots
      : selectableBots;
  const isLoadingBots =
    memberTab === 'friends'
      ? isLoadingFriends
      : filterMode === 'name'
      ? botSearch.trim()
        ? isLoadingNameSearch
        : isLoadingAvailableBots
      : botSearch.trim()
      ? isLoadingSmartSearch
      : isLoadingAvailableBots;
  const originatorBindingBot =
    actorKind === 'bot' &&
    originator?.bot_uuid &&
    !originator.bot_uuid.startsWith('human_')
      ? {
          bot_uuid: originator.bot_uuid,
          bot_name: originator.bot_name || originator.bot_uuid,
          summary: undefined,
          avatar_url: originator.avatar_url,
          visibility: originator.visibility,
          is_friend: null,
          is_online: originator.is_online,
          dynamic_status: originator.dynamic_status,
        }
      : null;
  const stateMachineFilteredBots = originatorBindingBot
    ? [
        originatorBindingBot,
        ...filteredBots.filter(
          (bot) => bot.bot_uuid !== originatorBindingBot.bot_uuid,
        ),
      ]
    : filteredBots;

  // 名称搜索：调用 Actor API
  const handleNameSearch = useCallback(
    async (name: string) => {
      if (!originator?.bot_uuid) return;
      setIsLoadingNameSearch(true);
      try {
        // 使用 Actor API（cooperatable_only=true 用于获取可协作 Bot）
        const response = await loadActors({
          currentBotUuid: originator.bot_uuid,
          cooperatableOnly: true,
          pageNo: 1,
          pageSize: 50,
          name: name || undefined,
        });
        // 定义映射函数，将 ActorBot 转换为 UI 格式
        const bots = (response.bots || []).map((bot: ActorBot) => ({
          bot_uuid: bot.bot_uuid,
          bot_name: bot.bot_name || bot.capabilities?.name || bot.bot_uuid,
          summary: bot.summary ?? bot.capabilities?.description ?? undefined,
          avatar_url: bot.avatar_url,
          visibility: (bot.visibility as 'public' | 'protected') ?? 'public',
          is_friend: bot.is_friend,
          short_profile: (bot as any).short_profile,
          dynamic_status: bot.dynamic_status,
          tags: (bot as any).tags,
        }));
        setNameSearchBots(bots);
      } catch (error) {
        console.error('[CreateGroupModal] Name search failed:', error);
        setNameSearchBots([]);
      } finally {
        setIsLoadingNameSearch(false);
      }
    },
    [originator?.bot_uuid, loadActors],
  );

  // 标记是否为首次智能搜索（用于1000ms防抖）
  const isFirstSmartSearch = useRef(true);
  // 记录最后一次回车搜索的内容，用于跳过防抖重复请求
  const lastEnterSearchRef = useRef<string>('');

  // 智能搜索：调用 Actor API（searchActors 语义搜索）
  const handleSmartSearch = useCallback(
    async (query: string) => {
      if (!originator?.bot_uuid) return;
      setIsLoadingSmartSearch(true);
      requestStartTimeRef.current = Date.now();

      // 记录当前搜索的 bots，用于后续的 position 查找
      currentBotsRef.current = [];
      currentTraceIdRef.current = null;

      try {
        // 使用 Actor API 智能搜索（searchActors）
        const searchResponse = await searchActors({
          currentBotUuid: originator.bot_uuid,
          cooperatableOnly: true,
          q: query || '',
        });

        const latencyMs = Date.now() - requestStartTimeRef.current;

        const bots = (searchResponse.bots || []).map((bot, index) => ({
          bot_uuid: bot.bot_uuid,
          bot_name: bot.bot_name || bot.capabilities?.name || bot.bot_uuid,
          summary: bot.summary ?? bot.capabilities?.description ?? undefined,
          avatar_url: bot.avatar_url,
          visibility: (bot.visibility as 'public' | 'protected') ?? 'public',
          is_friend: bot.is_friend,
          short_profile: (bot as any).short_profile,
          tags: (bot as any).tags,
          dynamic_status: bot.dynamic_status,
          // 记录位置信息用于埋点
          _position: index,
        }));
        setSmartSearchBots(bots);

        // 保存当前搜索结果信息用于埋点
        currentBotsRef.current = bots.map((bot) => ({
          bot_uuid: bot.bot_uuid,
          position: bot._position,
        }));
        // trace_id 来源: response.context?.recommend_response?.trace_id || response.trace_id
        const traceId =
          searchResponse?.context?.recommend_response?.trace_id ||
          searchResponse?.trace_id;
        currentTraceIdRef.current = traceId || null;

        // query_result 埋点：上报智能搜索结果
        if (trackerRef.current) {
          trackerRef.current.onQueryResult({
            trace_id: traceId,
            type: 'recommend', // 成员bot智能搜索的类型是 'recommend'
            keyword: query,
            bot_count: bots.length,
            latency_ms: latencyMs,
            bots: bots.map((b) => ({ bot_id: b.bot_uuid })),
          });
        }
      } catch (error) {
        console.error('[CreateGroupModal] Smart search failed:', error);
        setSmartSearchBots([]);
      } finally {
        setIsLoadingSmartSearch(false);
      }
    },
    [originator?.bot_uuid, searchActors],
  );

  // 智能推荐：根据协作目标或协作室名称推荐 Bot
  // 使用 Actor API（searchActors 语义搜索）
  const handleSmartRecommend = useCallback(async () => {
    // 优先使用协作目标，其次使用协作室名称
    const recommendQuery = description.trim() || topic.trim();
    if (!originator?.bot_uuid || !recommendQuery) {
      return;
    }
    setIsRecommending(true);
    const requestStartTime = Date.now();

    try {
      // 使用 Actor API 智能搜索
      const searchResponse = await searchActors({
        currentBotUuid: originator.bot_uuid,
        cooperatableOnly: true,
        q: recommendQuery,
      });

      const latencyMs = Date.now() - requestStartTime;

      const bots = (searchResponse.bots || []).map((bot, index) => ({
        bot_uuid: bot.bot_uuid,
        bot_name: bot.bot_name || bot.capabilities?.name || bot.bot_uuid,
        summary: bot.summary ?? bot.capabilities?.description ?? undefined,
        avatar_url: bot.avatar_url,
        visibility: (bot.visibility as 'public' | 'protected') ?? 'public',
        is_friend: bot.is_friend,
        short_profile: (bot as any).short_profile,
        _position: index,
      }));

      // 保存当前搜索结果信息用于埋点
      currentBotsRef.current = bots.map((bot) => ({
        bot_uuid: bot.bot_uuid,
        position: bot._position,
      }));
      // trace_id 来源: response.context?.recommend_response?.trace_id || response.trace_id
      const traceId =
        searchResponse?.context?.recommend_response?.trace_id ||
        searchResponse?.trace_id;
      currentTraceIdRef.current = traceId || null;

      // query_result 埋点：上报智能推荐结果
      if (trackerRef.current) {
        trackerRef.current.onQueryResult({
          trace_id: traceId,
          type: 'recommend',
          keyword: recommendQuery,
          bot_count: bots.length,
          latency_ms: latencyMs,
          bots: bots.map((b) => ({ bot_id: b.bot_uuid })),
        });
      }

      // 自动选中推荐的 Bot（最多选到上限）
      const remaining = MAX_MEMBER_BOTS - consultantBots.length;
      const recommendedUuids = bots
        .filter((bot) => bot.bot_uuid !== originator?.bot_uuid)
        .slice(0, Math.min(5, remaining))
        .map((bot) => bot.bot_uuid);

      // 上报 bot_select 埋点（自动推荐的视为 add）
      if (trackerRef.current) {
        recommendedUuids.forEach((botId) => {
          const bot = bots.find((b) => b.bot_uuid === botId);
          if (bot) {
            trackerRef.current!.onBotSelect(botId, 'add', bot._position);
          }
        });
      }

      setConsultantBots((prev) => {
        const newSet = new Set(prev);
        recommendedUuids.forEach((uuid) => {
          newSet.add(uuid);
          const bot = bots.find((b) => b.bot_uuid === uuid);
          if (bot) selectedBotCacheRef.current.set(uuid, bot);
        });
        return Array.from(newSet);
      });
      // 同时更新智能搜索结果
      setSmartSearchBots(bots);
      // 切换到可协作Bot + 按画像筛选
      setMemberTab('collaborate');
      setFilterMode('profile');
    } catch (error) {
      console.error('[CreateGroupModal] Smart recommend failed:', error);
    } finally {
      setIsRecommending(false);
    }
  }, [originator?.bot_uuid, topic, description, searchActors]);

  // 加载我的好友列表（bot 模式）或我的 Bot 列表（human 模式）
  const handleLoadFriends = useCallback(async () => {
    if (!originator?.bot_uuid) return;
    setIsLoadingFriends(true);
    try {
      if (actorKind === 'human') {
        // human 模式：加载用户拥有的 Bot 列表
        const response = await BcnController.getMyBots();
        const items = response?.items || response || [];
        const bots = (Array.isArray(items) ? items : [])
          .filter(
            (bot) =>
              bot.bot_uuid !== originator.bot_uuid &&
              !bot.bot_uuid?.startsWith('human_'),
          )
          .map((bot) => ({
            bot_uuid: bot.bot_uuid,
            bot_name: bot.capabilities?.name || bot.bot_uuid,
            summary: bot.capabilities?.summary ?? undefined,
            avatar_url: bot.avatar_url,
            visibility:
              (bot.capabilities?.visibility as 'public' | 'protected') ??
              'public',
            is_friend: null,
            is_online: computeIsOnline({
              status: bot.status,
              visibility:
                (bot.capabilities?.visibility as 'public' | 'protected') ??
                'public',
              dynamic_status: bot.dynamic_status,
            }),
            dynamic_status: bot.dynamic_status,
          }));
        setFriendBots(bots);
      } else {
        // bot 模式：加载好友列表
        const response = await BcnController.getFriends({
          bot_uuid: originator.bot_uuid,
        });
        const list = response?.friends || response?.data || [];
        const bots = list
          .filter((bot) => bot.bot_uuid !== originator.bot_uuid)
          .map((bot) => ({
            bot_uuid: bot.bot_uuid,
            bot_name: bot.name,
            summary: bot.summary ?? undefined,
            avatar_url: undefined,
            visibility: 'protected' as const,
            is_friend: true,
            is_online: bot.is_online,
            dynamic_status: bot.dynamic_status,
          }));
        setFriendBots(bots);
      }
    } catch (error) {
      console.error('[CreateGroupModal] Load friends/my bots failed:', error);
      setFriendBots([]);
    } finally {
      setIsLoadingFriends(false);
    }
  }, [originator?.bot_uuid, actorKind]);

  // Tab 切换时重置搜索结果
  useEffect(() => {
    if (memberTab === 'friends') {
      // 切换到我的好友时，清空搜索词
      setBotSearch('');
    } else if (memberTab === 'collaborate') {
      // 切换到可协作Bot时，根据 filterMode 处理
      if (filterMode === 'name') {
        setNameSearchBots([]);
        setSmartSearchBots([]);
        setBotSearch('');
      } else {
        setSmartSearchBots([]);
        setNameSearchBots([]);
      }
    }
  }, [memberTab, filterMode]);

  // 切换到好友 tab 时加载数据
  useEffect(() => {
    if (memberTab === 'friends' && open && originator?.bot_uuid) {
      handleLoadFriends();
    }
  }, [memberTab, open, originator?.bot_uuid, handleLoadFriends]);

  // 名称搜索防抖（按名称筛选模式下）
  useEffect(() => {
    if (memberTab !== 'collaborate' || filterMode !== 'name') return;
    if (!botSearch.trim()) {
      // 清空搜索词时，清空搜索结果，显示初始列表
      setNameSearchBots([]);
      return;
    }
    const timer = setTimeout(() => {
      handleNameSearch(botSearch);
    }, 300);
    return () => clearTimeout(timer);
  }, [botSearch, memberTab, filterMode, handleNameSearch]);

  // 智能搜索防抖（按画像筛选模式下）
  useEffect(() => {
    if (memberTab !== 'collaborate' || filterMode !== 'profile') return;
    if (!botSearch.trim()) {
      // 无搜索词时，清空搜索结果，显示全量数据
      setSmartSearchBots([]);
      isFirstSmartSearch.current = true;
      return;
    }
    // 如果与最后一次回车搜索内容相同，跳过防抖请求（避免重复）
    if (botSearch === lastEnterSearchRef.current) {
      return;
    }
    const timer = setTimeout(
      () => {
        handleSmartSearch(botSearch);
        isFirstSmartSearch.current = false;
      },
      isFirstSmartSearch.current ? 300 : 1000,
    );
    return () => clearTimeout(timer);
  }, [botSearch, memberTab, filterMode, handleSmartSearch]);

  // 滚动到底部触发加载更多（仅按名称筛选模式且无搜索词时）
  const handleListScroll = () => {
    if (memberTab !== 'collaborate' || filterMode !== 'name') return;
    // 有搜索词时使用 API 搜索，不支持分页
    if (botSearch.trim()) return;
    const el = listRef.current;
    if (!el || !hasMoreAvailableBots || isLoadingMoreBots) return;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 40) {
      loadMoreAvailableBots(originator?.bot_uuid);
    }
  };

  // 自动加载更多：当可协作Bot列表内容未填满容器或接近底部时，自动继续加载
  useEffect(() => {
    const checkAndLoadMore = () => {
      // 只在可协作Bot Tab 且按名称筛选模式且无搜索词时执行
      if (
        memberTab !== 'collaborate' ||
        filterMode !== 'name' ||
        botSearch.trim()
      )
        return;

      const container = listRef.current;
      if (!container) return;

      // 距离底部阈值（像素）
      const threshold = 100;
      const { scrollHeight, clientHeight, scrollTop } = container;
      const distanceToBottom = scrollHeight - scrollTop - clientHeight;

      // 两种情况触发加载：
      // 1. 无滚动条（内容未填满容器）
      // 2. 有滚动条但距离底部小于阈值（接近底部）
      const shouldLoadMore =
        scrollHeight <= clientHeight || distanceToBottom < threshold;

      if (
        shouldLoadMore &&
        hasMoreAvailableBots &&
        !isLoadingMoreBots &&
        !isLoadingAvailableBots &&
        availableBots.length > 0
      ) {
        loadMoreAvailableBots(originator?.bot_uuid);
      }
    };

    // 延迟执行，确保 DOM 已更新
    const timer = setTimeout(checkAndLoadMore, 100);
    return () => clearTimeout(timer);
  }, [
    memberTab,
    filterMode,
    botSearch,
    availableBots,
    hasMoreAvailableBots,
    isLoadingMoreBots,
    isLoadingAvailableBots,
    loadMoreAvailableBots,
    originator?.bot_uuid,
  ]);

  const resetForm = () => {
    setTopic('');
    setDescription('');
    setConsultantBots([]);
    setBotSearch('');
    setError('');
    setMemberTab('friends');
    setFilterMode('name');
    setNameSearchBots([]);
    setSmartSearchBots([]);
    setFriendBots([]);
    collaborationTemplatesRequestIdRef.current += 1;
    collaborationYamlRequestIdRef.current += 1;
    cancelCollaborationYamlValidation();
    setAutoReply(false);
    setGroupStrategy('chat');
    collaborationYamlTemplateRef.current = '';
    collaborationDefinitionYamlRef.current = '';
    setCollaborationYamlTemplate('');
    setCollaborationTemplates(null);
    setHasLoadedCollaborationTemplates(false);
    setIsLoadingCollaborationTemplates(false);
    setIsLoadingCollaborationYaml(false);
    setCollaborationTemplateDropdownOpen(false);
    setCollaborationDefinitionYaml('');
    setValidatedCollaborationYaml('');
    setValidatedCollaborationGraph(null);
    setValidatedCollaborationSummary(null);
    setCollaborationAuthoringStage('yaml');
    setSelectedCollaborationNodeId(undefined);
    setIsCompactCollaborationFlowOpen(false);
    setParticipantDefinitions([]);
    setActiveParticipantKey('');
    setParticipantBindings({});
    setMasterBot('');
    setMasterBotDropdownOpen(false);
    setDriverBotId('');
    setDriverBotDropdownOpen(false);
    selectedBotCacheRef.current.clear();
  };

  const handleClose = () => {
    resetForm();
    onClose();
  };

  // 从各 Bot 列表中查找 Bot 信息
  const getBotInfo = (botId: string) =>
    botId === originator?.bot_uuid
      ? {
          bot_uuid: originator.bot_uuid,
          bot_name: originator.bot_name || originator.bot_uuid,
          avatar_url: originator.avatar_url,
          dynamic_status: originator.dynamic_status,
          is_online: originator.is_online,
        }
      : availableBots.find((b) => b.bot_uuid === botId) ||
        nameSearchBots.find((b) => b.bot_uuid === botId) ||
        smartSearchBots.find((b) => b.bot_uuid === botId) ||
        friendBots.find((b) => b.bot_uuid === botId) ||
        selectedBotCacheRef.current.get(botId);

  const getBotName = (botId: string) => getBotInfo(botId)?.bot_name || botId;

  const collaborationBindingViews: Record<string, CollaborationBindingView> =
    buildCollaborationBindingViews(
      participantDefinitions,
      participantBindings,
      (botId) => getBotInfo(botId)?.bot_name,
    );

  const handleCollaborationNodeSelect = (nodeId: string) => {
    setSelectedCollaborationNodeId(nodeId);
    const node = validatedCollaborationGraph?.nodes.find(
      (candidate) => candidate.node_id === nodeId,
    );
    if (!node?.assignee || node.assignee.type !== 'bot_binding') return;

    const binding = node.assignee.binding;
    if (
      !participantDefinitions.some((participant) => participant.key === binding)
    ) {
      console.warn(
        `[CreateGroupModal] Collaboration graph references unknown participant binding: ${binding}`,
      );
      return;
    }

    setActiveParticipantKey(binding);
    window.requestAnimationFrame(() => {
      participantTabButtonRefs.current.get(binding)?.focus();
    });
  };

  const clearCollaborationYamlBindingState = useCallback(() => {
    cancelCollaborationYamlValidation();
    setValidatedCollaborationYaml('');
    setValidatedCollaborationGraph(null);
    setValidatedCollaborationSummary(null);
    setCollaborationAuthoringStage('yaml');
    setSelectedCollaborationNodeId(undefined);
    setIsCompactCollaborationFlowOpen(false);
    setParticipantDefinitions([]);
    setActiveParticipantKey('');
    setParticipantBindings({});
  }, [cancelCollaborationYamlValidation]);

  useEffect(() => {
    if (!canCreateStateMachineGroup && groupStrategy === 'state_machine') {
      setGroupStrategy('chat');
      clearCollaborationYamlBindingState();
      setError('');
    }
  }, [
    canCreateStateMachineGroup,
    clearCollaborationYamlBindingState,
    groupStrategy,
  ]);

  const resolveCollaborationTemplateLanguage = useCallback(
    (template: BcnController.CollaborationTemplateSummary, lang?: string) => {
      const defaultLanguage = lang || collaborationTemplateLanguage;
      if (template.available_languages?.includes(defaultLanguage)) {
        return defaultLanguage;
      }
      return template.available_languages?.[0] || defaultLanguage;
    },
    [collaborationTemplateLanguage],
  );

  const loadCollaborationTemplateYaml = useCallback(
    async (
      template: BcnController.CollaborationTemplateSummary,
      lang?: string,
    ) => {
      const requestId = collaborationYamlRequestIdRef.current + 1;
      collaborationYamlRequestIdRef.current = requestId;

      collaborationYamlTemplateRef.current = template.id;
      collaborationDefinitionYamlRef.current = '';
      setCollaborationYamlTemplate(template.id);
      setCollaborationDefinitionYaml('');
      setCollaborationTemplateDropdownOpen(false);
      clearCollaborationYamlBindingState();
      setIsLoadingCollaborationYaml(true);
      setError('');

      try {
        const yaml = await BcnController.getCollaborationTemplateYaml({
          template_id: template.id,
          lang: resolveCollaborationTemplateLanguage(template, lang),
        });
        if (requestId !== collaborationYamlRequestIdRef.current) return;
        collaborationDefinitionYamlRef.current = yaml;
        setCollaborationDefinitionYaml(yaml);
      } catch (err) {
        if (requestId !== collaborationYamlRequestIdRef.current) return;
        console.error(
          '[CreateGroupModal] Failed to load collaboration template yaml:',
          err,
        );
        collaborationDefinitionYamlRef.current = '';
        setCollaborationDefinitionYaml('');
        setError('协作模板内容加载失败，请稍后重试');
      } finally {
        if (requestId === collaborationYamlRequestIdRef.current) {
          setIsLoadingCollaborationYaml(false);
        }
      }
    },
    [clearCollaborationYamlBindingState, resolveCollaborationTemplateLanguage],
  );

  const loadCollaborationTemplates = useCallback(async () => {
    if (isLoadingCollaborationTemplates) return;

    const requestId = collaborationTemplatesRequestIdRef.current + 1;
    collaborationTemplatesRequestIdRef.current = requestId;
    setIsLoadingCollaborationTemplates(true);
    setError('');
    try {
      const response = await BcnController.getCollaborationTemplates();
      if (requestId !== collaborationTemplatesRequestIdRef.current) return;
      const templates = [...(response.templates || [])].sort(
        (a, b) => (a.priority ?? 0) - (b.priority ?? 0),
      );
      const normalizedResponse = {
        ...response,
        templates,
      };

      setCollaborationTemplates(normalizedResponse);
      if (!templates.length) {
        return;
      }

      if (
        !collaborationYamlTemplateRef.current &&
        !collaborationDefinitionYamlRef.current.trim()
      ) {
        await loadCollaborationTemplateYaml(
          templates[0],
          normalizedResponse.default_language,
        );
      }
    } catch (err) {
      if (requestId !== collaborationTemplatesRequestIdRef.current) return;
      console.error(
        '[CreateGroupModal] Failed to load collaboration templates:',
        err,
      );
      setCollaborationTemplates(null);
      setError('协作模板加载失败，请稍后重试');
    } finally {
      if (requestId === collaborationTemplatesRequestIdRef.current) {
        setHasLoadedCollaborationTemplates(true);
        setIsLoadingCollaborationTemplates(false);
      }
    }
  }, [isLoadingCollaborationTemplates, loadCollaborationTemplateYaml]);

  useEffect(() => {
    if (
      !open ||
      groupStrategy !== 'state_machine' ||
      hasLoadedCollaborationTemplates ||
      collaborationTemplates ||
      isLoadingCollaborationTemplates
    ) {
      return;
    }
    loadCollaborationTemplates();
  }, [
    collaborationTemplates,
    groupStrategy,
    hasLoadedCollaborationTemplates,
    isLoadingCollaborationTemplates,
    loadCollaborationTemplates,
    open,
  ]);

  const handleCollaborationTemplateModeClick = () => {
    if (
      isCollaborationTemplateMode &&
      (selectedCollaborationTemplate || isLoadingCollaborationYaml)
    ) {
      return;
    }

    collaborationYamlTemplateRef.current = '';
    collaborationDefinitionYamlRef.current = '';
    setCollaborationYamlTemplate('');
    setCollaborationDefinitionYaml('');
    setCollaborationTemplateDropdownOpen(false);
    clearCollaborationYamlBindingState();
    setError('');

    const firstTemplate = collaborationTemplateList[0];
    if (firstTemplate) {
      void loadCollaborationTemplateYaml(firstTemplate);
      return;
    }

    if (!isLoadingCollaborationTemplates) {
      void loadCollaborationTemplates();
    }
  };

  const handleCollaborationYamlTemplateChange = (templateId: string) => {
    if (templateId === FREE_COLLABORATION_TEMPLATE_ID) {
      if (collaborationYamlTemplate === FREE_COLLABORATION_TEMPLATE_ID) {
        setCollaborationTemplateDropdownOpen(false);
        return;
      }

      collaborationYamlRequestIdRef.current += 1;
      collaborationYamlTemplateRef.current = FREE_COLLABORATION_TEMPLATE_ID;
      collaborationDefinitionYamlRef.current = '';
      setCollaborationYamlTemplate(FREE_COLLABORATION_TEMPLATE_ID);
      setCollaborationDefinitionYaml('');
      setCollaborationTemplateDropdownOpen(false);
      clearCollaborationYamlBindingState();
      setIsLoadingCollaborationYaml(false);
      setError('');
      return;
    }

    const template = collaborationTemplateList.find(
      (item) => item.id === templateId,
    );
    if (!template) return;
    if (
      template.id === collaborationYamlTemplate &&
      collaborationDefinitionYaml.trim()
    ) {
      setCollaborationTemplateDropdownOpen(false);
      return;
    }

    void loadCollaborationTemplateYaml(template);
  };

  const handleCollaborationYamlChange = (value: string) => {
    collaborationDefinitionYamlRef.current = value;
    setCollaborationDefinitionYaml(value);
    if (value !== validatedCollaborationYaml) {
      clearCollaborationYamlBindingState();
      setError('');
    }
  };

  const handleValidateCollaborationYaml = async () => {
    const definitionYaml = collaborationDefinitionYaml;
    if (!definitionYaml.trim()) {
      setError('请输入自定义协作 YAML');
      return;
    }

    setError('');
    setValidatedCollaborationYaml('');
    try {
      const response = await validateCollaborationYaml(definitionYaml);
      if (
        !response ||
        collaborationDefinitionYamlRef.current !== definitionYaml
      ) {
        return;
      }

      if (!response.valid) {
        clearCollaborationYamlBindingState();
        setError(formatCollaborationValidationErrors(response.errors));
        return;
      }

      const definitions = buildCollaborationParticipantDefinitions(
        response.participants,
      );
      if (!definitions.length) {
        clearCollaborationYamlBindingState();
        setError('YAML 校验通过，但未返回可绑定的 participant');
        return;
      }

      setParticipantDefinitions(definitions);
      setParticipantBindings((prev) => {
        const next: Record<string, string[]> = {};
        definitions.forEach((definition) => {
          next[definition.key] = (prev[definition.key] || []).slice(0, 1);
        });
        return next;
      });
      setActiveParticipantKey((prev) =>
        definitions.some((definition) => definition.key === prev)
          ? prev
          : definitions[0].key,
      );
      setValidatedCollaborationYaml(definitionYaml);
      setValidatedCollaborationGraph(response.graph ?? null);
      setValidatedCollaborationSummary(response.summary);
      setSelectedCollaborationNodeId(undefined);
      setIsCompactCollaborationFlowOpen(false);
      setCollaborationAuthoringStage('bindings');
      if (!response.graph) {
        console.warn(
          '[CreateGroupModal] Valid collaboration response omitted graph; falling back to bindings',
        );
      }
      setError('');
    } catch (err) {
      if (collaborationDefinitionYamlRef.current !== definitionYaml) {
        return;
      }
      clearCollaborationYamlBindingState();
      setError(err instanceof Error ? err.message : 'YAML 校验请求失败');
    }
  };

  const toggleBindingBot = (botId: string, position?: number) => {
    if (!activeParticipantKey) {
      setError('请先选择要绑定的 participant');
      return;
    }

    const activeBotIds = participantBindings[activeParticipantKey] || [];
    const isSelected = activeBotIds.includes(botId);

    let finalPosition = position;
    if (finalPosition === undefined) {
      const found = currentBotsRef.current.find((b) => b.bot_uuid === botId);
      finalPosition = found?.position ?? -1;
    }

    if (trackerRef.current) {
      trackerRef.current.onBotSelect(
        botId,
        isSelected ? 'remove' : 'add',
        finalPosition,
      );
    }

    setParticipantBindings((prev) => {
      const current = prev[activeParticipantKey] || [];
      const nextBotIds = current.includes(botId) ? [] : [botId];
      const info = getBotInfo(botId);
      if (info) selectedBotCacheRef.current.set(botId, info);
      return {
        ...prev,
        [activeParticipantKey]: nextBotIds,
      };
    });
  };

  const toggleBot = (botId: string, position?: number) => {
    if (groupStrategy === 'state_machine') {
      toggleBindingBot(botId, position);
      return;
    }

    const isSelected = consultantBots.includes(botId);

    // 查找 position（如果未传入）
    let finalPosition = position;
    if (finalPosition === undefined) {
      const found = currentBotsRef.current.find((b) => b.bot_uuid === botId);
      finalPosition = found?.position ?? -1;
    }

    // bot_select 埋点：即使没有 trace_id 也上报
    if (trackerRef.current) {
      trackerRef.current.onBotSelect(
        botId,
        isSelected ? 'remove' : 'add',
        finalPosition,
      );
    }

    setConsultantBots((prev) => {
      if (prev.includes(botId)) {
        selectedBotCacheRef.current.delete(botId);
        return prev.filter((id) => id !== botId);
      }
      if (prev.length >= MAX_MEMBER_BOTS) {
        return prev;
      }
      const info = getBotInfo(botId);
      if (info) selectedBotCacheRef.current.set(botId, info);
      return [...prev, botId];
    });

    // 如果移除的 Bot 是已选的主节点，自动清空主节点
    if (isSelected && botId === masterBot) {
      setMasterBot('');
    }
  };

  // 判断Bot是否在线（可协作bot用dynamic_status，好友用is_online）
  const isBotOnline = (bot: InviteCandidateStatus): boolean => {
    if (bot.dynamic_status?.status === 'offline') {
      return false;
    }
    // 好友 tab 保留 is_online 语义；普通 Bot 列表不感知隐身状态
    if (bot.is_friend === true && bot.is_online !== undefined) {
      return bot.is_online === true;
    }
    // dynamic_status 缺失或 active 时临时视为可用
    return true;
  };

  const getUnavailableBotTip = (bot: InviteCandidateStatus): string => {
    if (bot.dynamic_status?.status === 'offline') {
      return '当前 Bot 与协作网络连接异常，暂无法加入新协作';
    }
    return '当前 Bot 为隐身状态或与协作网络连接异常，暂无法加入新协作';
  };

  // 校验参与者是否可邀请
  const validateParticipants = (
    botIds: string[] = consultantBots,
  ): { valid: boolean; error?: string } => {
    for (const botId of botIds) {
      const bot = getBotInfo(botId);
      if (!bot) continue;

      // 只有在线状态的Bot才能被选择
      if (!isBotOnline(bot)) {
        return {
          valid: false,
          error: `Bot「${bot.bot_name}」暂无法选择：${getUnavailableBotTip(
            bot,
          )}`,
        };
      }
    }
    return { valid: true };
  };

  const handleCreate = async () => {
    const isManagerWorker = groupStrategy === 'manager_worker';
    const isStateMachine = groupStrategy === 'state_machine';

    if (!originator) {
      setError('暂无可用的群主 Bot');
      return;
    }

    // 必须选择群主 Bot（任务协作型由 masterBot 兼任，其他类型需要 driverBotId）
    if (!isManagerWorker && !isStateMachine && !driverBotId) {
      setError('请选择群主 Bot');
      return;
    }

    if (!isStateMachine && consultantBots.length > MAX_MEMBER_BOTS) {
      setError(`最多选择 ${MAX_MEMBER_BOTS} 个成员 Bot`);
      return;
    }

    // 任务协作群必须选择主节点
    if (groupStrategy === 'manager_worker' && !masterBot) {
      setError('请选择主节点（Manager Bot）');
      return;
    }

    if (isStateMachine && !canCreateStateMachineGroup) {
      setError('用户视角暂不支持创建自定义协作群');
      return;
    }

    if (isStateMachine && !description.trim()) {
      setError('请输入协作目标');
      return;
    }

    if (
      isStateMachine &&
      (isCollaborationTemplateLoading || isValidatingCollaborationYaml)
    ) {
      setError('协作 YAML 处理中，请稍后再试');
      return;
    }

    if (isStateMachine && !collaborationDefinitionYaml.trim()) {
      setError('请输入自定义协作 YAML');
      return;
    }

    if (isStateMachine && !isCollaborationYamlValidated) {
      setError('请先点击校验 YAML，并完成 participant 绑定');
      return;
    }

    if (isStateMachine) {
      if (missingParticipantBindingLabels.length > 0) {
        setError(
          `请先为流程所需 participant 绑定 Bot：${missingParticipantBindingLabels.join(
            '、',
          )}`,
        );
        return;
      }

      if (allBoundBotIds.length === 0) {
        setError('请至少绑定一个 Bot');
        return;
      }

      if (!isOriginatorBoundToParticipant) {
        setError(
          `请至少为发起方 Bot「${
            originator.bot_name || originator.bot_uuid
          }」绑定一个角色`,
        );
        return;
      }
    }

    const stateMachineDriverBot = isStateMachine
      ? participantBindings.coordinator?.[0] ||
        participantBindings.driver?.[0] ||
        allBoundBotIds[0] ||
        originator.bot_uuid
      : '';
    const stateMachineParticipantIds = isStateMachine
      ? Array.from(new Set([stateMachineDriverBot, ...allBoundBotIds]))
      : [];

    const validation = validateParticipants(
      isStateMachine ? stateMachineParticipantIds : consultantBots,
    );
    if (!validation.valid) {
      setError(validation.error || '校验失败');
      return;
    }

    setError('');

    if (isStateMachine) {
      const stateMachineParticipantBindings = participantDefinitions.reduce<
        NonNullable<CreateGroupParams['participant_bindings']>
      >((result, definition) => {
        const botIds = (participantBindings[definition.key] || []).slice(0, 1);
        if (botIds.length > 0) {
          result[definition.key] = {
            source: 'manual',
            bot_ids: botIds,
          };
        }
        return result;
      }, {});
      const stateMachineAutoLabel = formatAutoGroupLabel(
        stateMachineParticipantIds.map((botId) => getBotName(botId)),
        originator.bot_name || '自定义协作',
      );

      const result = await createGroup({
        label: topic.trim() || stateMachineAutoLabel,
        context: description.trim() || undefined,
        driver_bot: stateMachineDriverBot,
        originator: originator.bot_uuid,
        participants: stateMachineParticipantIds.map((botId) => ({
          bot_uuid: botId,
        })),
        participant_bindings: stateMachineParticipantBindings,
        group_strategy: 'state_machine',
        auto_start_on_service_invocation: true,
        collaboration_definition_yaml: collaborationDefinitionYaml,
      });
      if (result) {
        // 关闭副屏
        if (window.aixBridge?.closePanelForce) {
          window.aixBridge.closePanelForce();
        }
        resetForm();
        onSuccess(result);
      }
      return;
    }

    const effectiveDriverBotId = isManagerWorker ? masterBot : driverBotId;
    const effectiveDriverBotName = isManagerWorker
      ? getBotName(masterBot)
      : driverBotId === originator?.bot_uuid
      ? originator.bot_name
      : getBotName(driverBotId);

    const driverParticipant = {
      bot_uuid: effectiveDriverBotId,
      bot_name: effectiveDriverBotName,
      role: isManagerWorker
        ? masterBot === effectiveDriverBotId
          ? 'manager'
          : 'worker'
        : 'driver',
    };

    // bot 模式：当前 Bot 如果不是 driver/master，作为 consultant/worker 加入
    // human 模式：human 始终作为 consultant/worker 加入
    const isOriginatorNotDriver = effectiveDriverBotId !== originator?.bot_uuid;
    const consultantParticipants = [
      // 发起者自动加入（bot 模式下仅当不是 driver 时；human 模式下始终加入）
      ...(actorKind === 'human' || isOriginatorNotDriver
        ? [
            {
              bot_uuid: originator.bot_uuid,
              bot_name: originator.bot_name,
              role: (isManagerWorker
                ? 'worker'
                : 'consultant') as GroupMemberRole,
            },
          ]
        : []),
      ...consultantBots
        .filter(
          (botId) =>
            botId !== effectiveDriverBotId && botId !== originator?.bot_uuid,
        )
        .map((botId) => ({
          bot_uuid: botId,
          bot_name: getBotName(botId),
          role: isManagerWorker
            ? botId === masterBot
              ? 'manager'
              : 'worker'
            : 'consultant',
        })),
    ];

    // 未填名称时，自动用「成员Bot1、...、群主Bot」拼接，成员超过5个加「等」
    const allNames = [
      ...consultantParticipants.map((p) => p.bot_name),
      effectiveDriverBotName,
    ];
    const autoLabel = formatAutoGroupLabel(allNames, '协作群');

    const params: CreateGroupParams = {
      label: topic.trim() || autoLabel,
      context: description.trim() || undefined,
      driver_bot: effectiveDriverBotId,
      originator: originator.bot_uuid,
      participants: [driverParticipant, ...consultantParticipants],
      // 自由聊天群才设置路由策略
      routing_policy: !isManagerWorker
        ? {
            default_bot_final_delivery: autoReply
              ? 'send_to_driver'
              : 'inject_observers',
          }
        : undefined,
      group_strategy: groupStrategy,
      master_bot: isManagerWorker ? masterBot : undefined,
    };

    const result = await createGroup(params);
    if (result) {
      // 关闭副屏
      if (window.aixBridge?.closePanelForce) {
        window.aixBridge.closePanelForce();
      }
      resetForm();
      onSuccess(result);
    }
  };

  // 主节点下拉框：点击外部关闭
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        masterBotDropdownRef.current &&
        !masterBotDropdownRef.current.contains(e.target as Node)
      ) {
        setMasterBotDropdownOpen(false);
      }
    };
    if (masterBotDropdownOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [masterBotDropdownOpen]);

  // 群主 Bot 下拉框：点击外部关闭
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        driverBotDropdownRef.current &&
        !driverBotDropdownRef.current.contains(e.target as Node)
      ) {
        setDriverBotDropdownOpen(false);
      }
    };
    if (driverBotDropdownOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [driverBotDropdownOpen]);

  // 协同模板下拉框：点击外部关闭
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        collaborationTemplateDropdownRef.current &&
        !collaborationTemplateDropdownRef.current.contains(e.target as Node)
      ) {
        setCollaborationTemplateDropdownOpen(false);
      }
    };
    if (collaborationTemplateDropdownOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [collaborationTemplateDropdownOpen]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* 遮罩 - 仅用于视觉遮罩，点击不关闭 */}
      <div className="absolute inset-0 bg-black/30 backdrop-blur-sm" />

      {/* 弹窗内容：自适应高度，最大高度为视口高度减去边距 */}
      <div
        className={cn(
          'relative bg-white rounded-xl shadow-xl flex flex-col h-[calc(100vh-2rem)] min-h-[400px] max-h-[760px] transition-[width,max-width]',
          groupStrategy === 'state_machine' &&
            validatedCollaborationGraph &&
            collaborationAuthoringStage === 'bindings' &&
            !isCompactCollaborationFlowLayout
            ? 'w-[92vw] max-w-[1440px]'
            : 'w-full max-w-2xl',
        )}
      >
        {/* 头部 */}
        <div className="flex items-center justify-between px-6 py-3 border-b border-slate-100 flex-shrink-0">
          <div className="flex gap-3">
            <h2 className="text-base font-semibold text-slate-800">拉起协作</h2>
            <p className="text-xs text-slate-400 mt-0.5">
              选择 Bot 成员，开启多智能体协作
            </p>
          </div>
          <button
            type="button"
            onClick={handleClose}
            className="p-1.5 rounded-lg hover:bg-slate-100 transition-colors"
            data-aspm-click="ca114903.da194173"
            data-aspm-desc="GroupChat-关闭创建协作弹窗"
            data-aspm-param={``}
            data-aspm-expo
          >
            <X className="w-4 h-4 text-slate-400" />
          </button>
        </div>

        <CollaborationFlowWorkspace
          className="rounded-none bg-white"
          compact={isCompactCollaborationFlowLayout}
          compactPanelOpen={isCompactCollaborationFlowOpen}
          onCompactPanelOpenChange={setIsCompactCollaborationFlowOpen}
          panelMeta={
            validatedCollaborationGraph &&
            collaborationAuthoringStage === 'bindings' ? (
              <span className="text-xs text-slate-400">
                {validatedCollaborationGraph.nodes.length} 个节点
              </span>
            ) : null
          }
          panel={
            validatedCollaborationGraph &&
            collaborationAuthoringStage === 'bindings' ? (
              <CollaborationFlowPreviewLoader
                key={validatedCollaborationYaml}
                graph={validatedCollaborationGraph}
                initialNodes={
                  validatedCollaborationSummary?.initial_nodes ?? []
                }
                bindingViews={collaborationBindingViews}
                selectedNodeId={selectedCollaborationNodeId}
                highlightedBinding={activeParticipantKey}
                onNodeSelect={handleCollaborationNodeSelect}
              />
            ) : null
          }
        >
          {/* 上方：固定表单（名称 + 群主） */}
          <div className="px-6 pt-3 pb-2 flex-shrink-0 border-b border-slate-100">
            {/* 第一行：协作室名称 + 协作目标 */}
            <div className="flex gap-4 mb-2">
              {/* 协作室名称 */}
              <div className="flex-[1]">
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  协作群名称
                </label>
                <input
                  type="text"
                  value={topic}
                  onChange={(e) => setTopic(e.target.value)}
                  placeholder="留空则自动用 Bot 名称生成"
                  autoFocus
                  className="w-full px-3 py-1.5 text-xs bg-white border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-lavender-500/20 focus:border-lavender-400 transition-all"
                />
              </div>

              {/* 协作目标 */}
              <div className="flex-[1.5]">
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  协作目标
                  {groupStrategy === 'state_machine' && (
                    <span className="text-red-500 ml-0.5">*</span>
                  )}
                </label>
                <input
                  type="text"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="描述协作目标，用于智能推荐"
                  required={groupStrategy === 'state_machine'}
                  className="w-full px-3 py-1.5 text-xs bg-white border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-lavender-500/20 focus:border-lavender-400 transition-all"
                />
              </div>
            </div>

            {/* 协作群类型选择 */}
            <div className="mt-2">
              <label className="block text-sm font-medium text-slate-700 mb-1">
                协作群类型
              </label>
              <div
                className={cn(
                  'grid grid-cols-1 gap-2',
                  canCreateStateMachineGroup
                    ? 'sm:grid-cols-3'
                    : 'sm:grid-cols-2',
                )}
              >
                <label
                  className={cn(
                    'flex items-center gap-2 px-3 py-1.5 border rounded-lg cursor-pointer transition-all group min-w-0',
                    groupStrategy === 'chat'
                      ? 'border-lavender-400 bg-lavender-50/50 shadow-sm shadow-lavender-100'
                      : 'border-slate-200 hover:border-slate-300 hover:bg-slate-50/50',
                  )}
                >
                  {/* 自定义圆形 Radio 指示器 */}
                  <div
                    className={cn(
                      'w-3.5 h-3.5 rounded-full border-2 flex items-center justify-center flex-shrink-0 transition-all',
                      groupStrategy === 'chat'
                        ? 'border-lavender-500'
                        : 'border-slate-300 group-hover:border-slate-400',
                    )}
                  >
                    {groupStrategy === 'chat' && (
                      <div className="w-1.5 h-1.5 rounded-full bg-lavender-500" />
                    )}
                  </div>
                  <MessagesSquare
                    className={cn(
                      'w-3.5 h-3.5 flex-shrink-0 transition-colors',
                      groupStrategy === 'chat'
                        ? 'text-lavender-500'
                        : 'text-slate-400',
                    )}
                  />
                  <span
                    className={cn(
                      'text-xs font-medium transition-colors whitespace-nowrap flex-shrink-0',
                      groupStrategy === 'chat'
                        ? 'text-lavender-700'
                        : 'text-slate-800',
                    )}
                  >
                    自由聊天型
                  </span>
                  <span className="text-[11px] text-slate-400 truncate">
                    开放交流、灵活讨论
                  </span>
                  <input
                    type="radio"
                    name="groupStrategy"
                    value="chat"
                    checked={groupStrategy === 'chat'}
                    onChange={() => {
                      setGroupStrategy('chat');
                      setMasterBot('');
                      setMasterBotDropdownOpen(false);
                    }}
                    className="sr-only"
                  />
                </label>
                <label
                  className={cn(
                    'flex items-center gap-2 px-3 py-1.5 border rounded-lg cursor-pointer transition-all group min-w-0',
                    groupStrategy === 'manager_worker'
                      ? 'border-lavender-400 bg-lavender-50/50 shadow-sm shadow-lavender-100'
                      : 'border-slate-200 hover:border-slate-300 hover:bg-slate-50/50',
                  )}
                >
                  {/* 自定义圆形 Radio 指示器 */}
                  <div
                    className={cn(
                      'w-3.5 h-3.5 rounded-full border-2 flex items-center justify-center flex-shrink-0 transition-all',
                      groupStrategy === 'manager_worker'
                        ? 'border-lavender-500'
                        : 'border-slate-300 group-hover:border-slate-400',
                    )}
                  >
                    {groupStrategy === 'manager_worker' && (
                      <div className="w-1.5 h-1.5 rounded-full bg-lavender-500" />
                    )}
                  </div>
                  <Network
                    className={cn(
                      'w-3.5 h-3.5 flex-shrink-0 transition-colors',
                      groupStrategy === 'manager_worker'
                        ? 'text-lavender-500'
                        : 'text-slate-400',
                    )}
                  />
                  <span
                    className={cn(
                      'text-xs font-medium transition-colors whitespace-nowrap flex-shrink-0',
                      groupStrategy === 'manager_worker'
                        ? 'text-lavender-700'
                        : 'text-slate-800',
                    )}
                  >
                    任务协作型
                  </span>
                  <span className="text-[11px] text-slate-400 truncate">
                    主从模式
                  </span>
                  <input
                    type="radio"
                    name="groupStrategy"
                    value="manager_worker"
                    checked={groupStrategy === 'manager_worker'}
                    onChange={() => setGroupStrategy('manager_worker')}
                    className="sr-only"
                  />
                </label>
                {canCreateStateMachineGroup && (
                  <label
                    className={cn(
                      'flex items-center gap-2 px-3 py-1.5 border rounded-lg cursor-pointer transition-all group min-w-0',
                      groupStrategy === 'state_machine'
                        ? 'border-lavender-400 bg-lavender-50/50 shadow-sm shadow-lavender-100'
                        : 'border-slate-200 hover:border-slate-300 hover:bg-slate-50/50',
                    )}
                  >
                    <div
                      className={cn(
                        'w-3.5 h-3.5 rounded-full border-2 flex items-center justify-center flex-shrink-0 transition-all',
                        groupStrategy === 'state_machine'
                          ? 'border-lavender-500'
                          : 'border-slate-300 group-hover:border-slate-400',
                      )}
                    >
                      {groupStrategy === 'state_machine' && (
                        <div className="w-1.5 h-1.5 rounded-full bg-lavender-500" />
                      )}
                    </div>
                    <Workflow
                      className={cn(
                        'w-3.5 h-3.5 flex-shrink-0 transition-colors',
                        groupStrategy === 'state_machine'
                          ? 'text-lavender-500'
                          : 'text-slate-400',
                      )}
                    />
                    <span
                      className={cn(
                        'text-xs font-medium transition-colors whitespace-nowrap flex-shrink-0',
                        groupStrategy === 'state_machine'
                          ? 'text-lavender-700'
                          : 'text-slate-800',
                      )}
                    >
                      自定义协作
                    </span>
                    <span className="text-[11px] text-slate-400 truncate">
                      状态机编排
                    </span>
                    <input
                      type="radio"
                      name="groupStrategy"
                      value="state_machine"
                      checked={groupStrategy === 'state_machine'}
                      onChange={() => {
                        setGroupStrategy('state_machine');
                        setMasterBot('');
                        setMasterBotDropdownOpen(false);
                      }}
                      className="sr-only"
                    />
                  </label>
                )}
              </div>
              {groupStrategy === 'manager_worker' && (
                <div className="mt-2 flex items-start gap-1.5 rounded-lg border border-amber-200 bg-amber-50/70 px-2.5 py-2 text-[11px] leading-5 text-amber-700">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-amber-500" />
                  <div className="min-w-0">
                    <p className="font-medium">
                      {MANAGER_WORKER_ENGINE_SUPPORT_TIP}
                    </p>
                  </div>
                </div>
              )}
            </div>

            {groupStrategy === 'chat' ? (
              /* 自由聊天型：群主 Bot + 自动回复 */
              <div className="flex gap-4 mt-2">
                {/* 群主 Bot（可下拉选择） */}
                <div className="flex-[1] min-w-0">
                  <label className="block text-sm font-medium text-slate-700 mb-1">
                    群主 Bot
                  </label>
                  <div ref={driverBotDropdownRef} className="relative">
                    <button
                      type="button"
                      onClick={() =>
                        setDriverBotDropdownOpen(!driverBotDropdownOpen)
                      }
                      className={cn(
                        'w-full h-[46px] flex items-center gap-2 px-3 py-1.5 text-sm bg-white border rounded-lg transition-all text-left',
                        driverBotDropdownOpen
                          ? 'ring-2 ring-lavender-500/20 border-lavender-400'
                          : 'border-slate-200 hover:border-slate-300',
                      )}
                    >
                      {driverBotId ? (
                        <>
                          <BotAvatar
                            type="assistant"
                            size="sm"
                            name={
                              driverBotId === originator?.bot_uuid
                                ? originator?.bot_name
                                : getBotName(driverBotId)
                            }
                            botId={driverBotId?.split(':')[0]}
                            avatarUrl={
                              driverBotId === originator?.bot_uuid
                                ? originator?.avatar_url
                                : getBotInfo(driverBotId)?.avatar_url
                            }
                          />
                          <span className="flex-1 truncate font-medium text-slate-700 min-w-0">
                            {driverBotId === originator?.bot_uuid
                              ? originator?.bot_name
                              : getBotName(driverBotId)}
                          </span>
                          {driverBotId === originator?.bot_uuid && (
                            <span className="flex-shrink-0 inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-lavender-50 text-lavender-600">
                              当前
                            </span>
                          )}
                          {/* 当前 Bot 作为群主时的在线状态 */}
                          {driverBotId === originator?.bot_uuid ? (
                            isOriginatorUnreachable ? (
                              <TooltipProvider>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <span className="flex items-center gap-0.5 text-xs text-amber-500">
                                      <AlertTriangle className="w-3 h-3" />
                                      连接异常
                                    </span>
                                  </TooltipTrigger>
                                  <TooltipContent
                                    side="top"
                                    className="text-xs"
                                  >
                                    {COMMUNICATION_WARNING_TEXT}
                                  </TooltipContent>
                                </Tooltip>
                              </TooltipProvider>
                            ) : isOriginatorOnline ? (
                              <span className="flex items-center gap-0.5 text-xs text-green-500">
                                <Globe className="w-3 h-3" />
                                在线
                              </span>
                            ) : (
                              <span className="flex items-center gap-0.5 text-xs text-slate-400">
                                <EyeOff className="w-3 h-3" />
                                隐身
                              </span>
                            )
                          ) : null}
                        </>
                      ) : (
                        <span className="text-slate-400">选择群主 Bot</span>
                      )}
                      <svg
                        className={cn(
                          'w-4 h-4 text-slate-400 flex-shrink-0 transition-transform ml-auto',
                          driverBotDropdownOpen && 'rotate-180',
                        )}
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M19 9l-7 7-7-7"
                        />
                      </svg>
                    </button>
                    {driverBotDropdownOpen && (
                      <div className="absolute z-20 mt-1 w-full bg-white border border-slate-200 rounded-lg shadow-lg max-h-[240px] overflow-y-auto">
                        {/* 当前 Tab Bot（发起者）— human 模式下不显示（human 不能当 driver） */}
                        {actorKind === 'bot' && originator?.bot_uuid && (
                          <button
                            type="button"
                            onClick={() => {
                              setDriverBotId(originator.bot_uuid);
                              setDriverBotDropdownOpen(false);
                            }}
                            className={cn(
                              'w-full flex items-center gap-2.5 px-3 py-2 text-left transition-colors hover:bg-slate-50',
                              driverBotId === originator.bot_uuid &&
                                'bg-lavender-50/70',
                            )}
                          >
                            <BotAvatar
                              type="assistant"
                              size="sm"
                              name={originator.bot_name}
                              botId={originator.bot_uuid?.split(':')[0]}
                              avatarUrl={originator.avatar_url}
                            />
                            <span className="flex-1 truncate text-sm font-medium text-slate-700 min-w-0">
                              {originator.bot_name}
                            </span>
                            <span className="flex-shrink-0 inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-lavender-50 text-lavender-600">
                              当前
                            </span>
                            {driverBotId === originator.bot_uuid && (
                              <Check className="w-4 h-4 text-lavender-500 flex-shrink-0" />
                            )}
                          </button>
                        )}
                        {/* 分隔线 */}
                        {consultantBots.length > 0 &&
                          actorKind === 'bot' &&
                          originator?.bot_uuid && (
                            <div className="border-t border-slate-100" />
                          )}
                        {/* 已选成员 Bot（排除 human 类型） */}
                        {consultantBots
                          .filter((botId) => !botId.startsWith('human_'))
                          .map((botId) => {
                            const bot = getBotInfo(botId);
                            return (
                              <button
                                key={botId}
                                type="button"
                                onClick={() => {
                                  setDriverBotId(botId);
                                  setDriverBotDropdownOpen(false);
                                }}
                                className={cn(
                                  'w-full flex items-center gap-2.5 px-3 py-2 text-left transition-colors hover:bg-slate-50',
                                  driverBotId === botId && 'bg-lavender-50/70',
                                )}
                              >
                                <BotAvatar
                                  type="assistant"
                                  size="sm"
                                  name={getBotName(botId)}
                                  botId={botId?.split(':')[0]}
                                  avatarUrl={bot?.avatar_url}
                                />
                                <span className="flex-1 truncate text-sm font-medium text-slate-700 min-w-0">
                                  {getBotName(botId)}
                                </span>
                                {driverBotId === botId && (
                                  <Check className="w-4 h-4 text-lavender-500 flex-shrink-0" />
                                )}
                              </button>
                            );
                          })}
                        {(actorKind === 'human' || !originator?.bot_uuid) &&
                          consultantBots.filter(
                            (id) => !id.startsWith('human_'),
                          ).length === 0 && (
                            <div className="px-3 py-3 text-xs text-slate-400 text-center">
                              请先选择成员 Bot
                            </div>
                          )}
                      </div>
                    )}
                  </div>
                </div>

                {/* 自动回复开关 */}
                <div className="flex-[1.5] min-w-0">
                  <label className="block text-sm font-medium text-slate-700 mb-1">
                    自动回复
                  </label>
                  <div className="flex items-center gap-3 h-[46px] px-3 pl-1 min-w-0 overflow-hidden">
                    <button
                      type="button"
                      onClick={() => setAutoReply(!autoReply)}
                      className={cn(
                        'relative inline-flex h-5 w-9 items-center rounded-full transition-colors flex-shrink-0',
                        autoReply ? 'bg-lavender-500' : 'bg-slate-200',
                      )}
                      data-aspm-click="ca114903.da194174"
                      data-aspm-desc="GroupChat-切换自动回复"
                      data-aspm-param={``}
                      data-aspm-expo
                    >
                      <span
                        className={cn(
                          'inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform',
                          autoReply ? 'translate-x-5' : 'translate-x-0.5',
                        )}
                      />
                    </button>
                    <span className="text-xs text-slate-500 flex-1 truncate">
                      {autoReply
                        ? '群主 Bot 将默认回复每一条消息'
                        : '群主 Bot 仅在被@时或上下文语境高度关联时答复'}
                    </span>
                  </div>
                </div>
              </div>
            ) : groupStrategy === 'manager_worker' ? (
              /* 任务协作型：主节点（Manager Bot）独占一行，不显示群主 Bot */
              <div className="mt-2">
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  主节点（Manager Bot）
                </label>
                <div ref={masterBotDropdownRef} className="relative">
                  <button
                    type="button"
                    onClick={() =>
                      setMasterBotDropdownOpen(!masterBotDropdownOpen)
                    }
                    className={cn(
                      'w-full h-[46px] flex items-center gap-2 px-3 py-1.5 text-sm bg-white border rounded-lg transition-all text-left',
                      masterBotDropdownOpen
                        ? 'ring-2 ring-lavender-500/20 border-lavender-400'
                        : 'border-slate-200 hover:border-slate-300',
                    )}
                  >
                    {masterBot ? (
                      <>
                        <BotAvatar
                          type="assistant"
                          size="sm"
                          name={
                            masterBot === originator?.bot_uuid
                              ? originator?.bot_name
                              : getBotName(masterBot)
                          }
                          botId={masterBot?.split(':')[0]}
                          avatarUrl={
                            masterBot === originator?.bot_uuid
                              ? originator?.avatar_url
                              : getBotInfo(masterBot)?.avatar_url
                          }
                        />
                        <span className="flex-1 truncate font-medium text-slate-700 min-w-0">
                          {masterBot === originator?.bot_uuid
                            ? originator?.bot_name
                            : getBotName(masterBot)}
                        </span>
                        {masterBot === originator?.bot_uuid && (
                          <span className="flex-shrink-0 inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-lavender-50 text-lavender-600">
                            群主
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="text-slate-400">Manager Bot</span>
                    )}
                    <svg
                      className={cn(
                        'w-4 h-4 text-slate-400 flex-shrink-0 transition-transform ml-auto',
                        masterBotDropdownOpen && 'rotate-180',
                      )}
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M19 9l-7 7-7-7"
                      />
                    </svg>
                  </button>
                  {masterBotDropdownOpen && (
                    <div className="absolute z-20 mt-1 w-full bg-white border border-slate-200 rounded-lg shadow-lg max-h-[240px] overflow-y-auto">
                      {/* 群主 Bot 选项（仅 bot 模式显示） */}
                      {actorKind === 'bot' && originator?.bot_uuid && (
                        <button
                          type="button"
                          onClick={() => {
                            setMasterBot(originator.bot_uuid);
                            setMasterBotDropdownOpen(false);
                          }}
                          className={cn(
                            'w-full flex items-center gap-2.5 px-3 py-2 text-left transition-colors hover:bg-slate-50',
                            masterBot === originator.bot_uuid &&
                              'bg-lavender-50/70',
                          )}
                        >
                          <BotAvatar
                            type="assistant"
                            size="sm"
                            name={originator.bot_name}
                            botId={originator.bot_uuid?.split(':')[0]}
                            avatarUrl={originator.avatar_url}
                          />
                          <span className="flex-1 truncate text-sm font-medium text-slate-700 min-w-0">
                            {originator.bot_name}
                          </span>
                          <span className="flex-shrink-0 inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-lavender-50 text-lavender-600">
                            群主
                          </span>
                          {masterBot === originator.bot_uuid && (
                            <Check className="w-4 h-4 text-lavender-500 flex-shrink-0" />
                          )}
                        </button>
                      )}
                      {/* 成员 Bot 选项（排除 human） */}
                      {consultantBots.filter((id) => !id.startsWith('human_'))
                        .length > 0 &&
                        actorKind === 'bot' &&
                        originator?.bot_uuid && (
                          <div className="border-t border-slate-100" />
                        )}
                      {consultantBots
                        .filter((botId) => !botId.startsWith('human_'))
                        .map((botId) => {
                          const bot = getBotInfo(botId);
                          return (
                            <button
                              key={botId}
                              type="button"
                              onClick={() => {
                                setMasterBot(botId);
                                setMasterBotDropdownOpen(false);
                              }}
                              className={cn(
                                'w-full flex items-center gap-2.5 px-3 py-2 text-left transition-colors hover:bg-slate-50',
                                masterBot === botId && 'bg-lavender-50/70',
                              )}
                            >
                              <BotAvatar
                                type="assistant"
                                size="sm"
                                name={getBotName(botId)}
                                botId={botId?.split(':')[0]}
                                avatarUrl={bot?.avatar_url}
                              />
                              <span className="flex-1 truncate text-sm font-medium text-slate-700 min-w-0">
                                {getBotName(botId)}
                              </span>
                              {masterBot === botId && (
                                <Check className="w-4 h-4 text-lavender-500 flex-shrink-0" />
                              )}
                            </button>
                          );
                        })}
                      {consultantBots.filter((id) => !id.startsWith('human_'))
                        .length === 0 &&
                        (actorKind === 'human' || !originator?.bot_uuid) && (
                          <div className="px-3 py-3 text-xs text-slate-400 text-center">
                            请先选择成员 Bot
                          </div>
                        )}
                    </div>
                  )}
                </div>
                <p className="mt-1.5 text-[11px] text-slate-400">
                  {MANAGER_WORKER_DEFAULT_WORKER_TIP}
                </p>
              </div>
            ) : (
              /* 结构化协同：展示发起方 Bot */
              <div className="mt-2">
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  发起方 Bot
                </label>
                <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-50 border border-slate-200/60 rounded-lg min-w-0">
                  <BotAvatar
                    type="assistant"
                    size="sm"
                    name={originator?.bot_name}
                    botId={originator?.bot_uuid?.split(':')[0]}
                    avatarUrl={originator?.avatar_url}
                  />
                  <span className="text-sm text-slate-700 font-medium flex-1 truncate min-w-0">
                    {originator?.bot_name || '暂无可用 Bot'}
                  </span>
                  {isOriginatorUnreachable ? (
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span
                            className="flex items-center gap-0.5 text-xs text-amber-500"
                            aria-label={COMMUNICATION_WARNING_TEXT}
                          >
                            <AlertTriangle className="w-3 h-3" />
                            连接异常
                          </span>
                        </TooltipTrigger>
                        <TooltipContent side="top" className="text-xs">
                          {COMMUNICATION_WARNING_TEXT}
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  ) : isOriginatorOnline ? (
                    <span className="flex items-center gap-0.5 text-xs text-green-500">
                      <Globe className="w-3 h-3" />
                      在线
                    </span>
                  ) : (
                    <span className="flex items-center gap-0.5 text-xs text-slate-400">
                      <EyeOff className="w-3 h-3" />
                      隐身
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* 下方：Bot 选择区（flex-1，撑满剩余空间） */}
          <div className="flex-1 min-h-0 flex flex-col overflow-hidden px-6 py-2">
            {groupStrategy === 'state_machine' ? (
              <div className="flex-1 min-h-0 flex flex-col gap-3">
                {isCollaborationYamlValidated &&
                collaborationAuthoringStage !== 'yaml' ? (
                  <div className="flex flex-shrink-0 items-center justify-between gap-3 rounded-xl border border-slate-200/60 bg-white px-3 py-2">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="text-sm font-medium text-slate-700">
                        协同剧本
                      </span>
                      <span className="text-xs px-2 py-0.5 rounded-full text-green-600 bg-green-50">
                        已解析{' '}
                        {validatedCollaborationGraph?.nodes.length ??
                          validatedCollaborationSummary?.nodes ??
                          0}{' '}
                        个节点、{participantDefinitions.length} 个角色
                      </span>
                    </div>
                    <div className="flex flex-shrink-0 items-center gap-2">
                      <Button
                        variant="secondary"
                        soft
                        size="sm"
                        onClick={() => {
                          clearCollaborationYamlBindingState();
                          setError('');
                        }}
                      >
                        重新编辑
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="flex-1 min-h-0 flex flex-col">
                    <div className="flex items-center justify-between gap-2 mb-2 flex-shrink-0 min-w-0">
                      <div className="flex items-center gap-2 min-w-0 flex-1">
                        <label className="text-sm font-medium text-slate-700 whitespace-nowrap">
                          协同剧本
                          <span className="text-red-500 ml-0.5">*</span>
                        </label>
                        <div className="flex items-center bg-slate-100 rounded-lg p-0.5 flex-shrink-0">
                          <button
                            type="button"
                            onClick={() =>
                              handleCollaborationYamlTemplateChange(
                                FREE_COLLABORATION_TEMPLATE_ID,
                              )
                            }
                            className={cn(
                              'px-2.5 py-1 text-xs font-medium rounded-md transition-all whitespace-nowrap',
                              isFreeCollaborationTemplateSelected
                                ? 'bg-white text-slate-800 shadow-sm'
                                : 'text-slate-500 hover:text-slate-700',
                            )}
                          >
                            自由编辑
                          </button>
                          <button
                            type="button"
                            onClick={handleCollaborationTemplateModeClick}
                            className={cn(
                              'px-2.5 py-1 text-xs font-medium rounded-md transition-all whitespace-nowrap',
                              isCollaborationTemplateMode
                                ? 'bg-white text-slate-800 shadow-sm'
                                : 'text-slate-500 hover:text-slate-700',
                            )}
                          >
                            模板
                          </button>
                        </div>
                        {isCollaborationTemplateMode && (
                          <div
                            ref={collaborationTemplateDropdownRef}
                            className="relative min-w-[180px] max-w-[280px] flex-1"
                          >
                            <button
                              type="button"
                              onClick={() => {
                                if (
                                  hasLoadedCollaborationTemplates &&
                                  !collaborationTemplates &&
                                  !isLoadingCollaborationTemplates
                                ) {
                                  void loadCollaborationTemplates();
                                }
                                setCollaborationTemplateDropdownOpen(
                                  !collaborationTemplateDropdownOpen,
                                );
                              }}
                              className={cn(
                                'w-full h-8 flex items-center gap-2 px-2.5 text-left bg-white border rounded-lg transition-all',
                                collaborationTemplateDropdownOpen
                                  ? 'ring-2 ring-lavender-500/20 border-lavender-400'
                                  : 'border-slate-200 hover:border-slate-300',
                              )}
                            >
                              {selectedCollaborationTemplate ? (
                                <div className="min-w-0 flex-1 flex items-center gap-1.5">
                                  <span className="min-w-0 flex-1 truncate text-xs font-medium text-slate-700">
                                    {selectedCollaborationTemplate.name}
                                  </span>
                                  <div className="hidden md:flex items-center gap-1 flex-shrink-0">
                                    {selectedCollaborationTemplate.tags
                                      .slice(0, 2)
                                      .map((tag) => (
                                        <span
                                          key={tag}
                                          className={cn(
                                            'max-w-[60px] truncate rounded-full border px-1.5 py-0.5 text-[10px] font-medium leading-none',
                                            getCollaborationTagColorClass(tag),
                                          )}
                                        >
                                          {getCollaborationTagLabel(tag)}
                                        </span>
                                      ))}
                                  </div>
                                </div>
                              ) : isLoadingCollaborationTemplates ? (
                                <>
                                  <Loader2 className="w-3.5 h-3.5 animate-spin text-slate-400 flex-shrink-0" />
                                  <span className="flex-1 truncate text-xs text-slate-400">
                                    加载模板...
                                  </span>
                                </>
                              ) : (
                                <span className="flex-1 truncate text-xs text-slate-400">
                                  选择模板
                                </span>
                              )}
                              <ChevronDown
                                className={cn(
                                  'w-4 h-4 text-slate-400 flex-shrink-0 transition-transform',
                                  collaborationTemplateDropdownOpen &&
                                    'rotate-180',
                                )}
                              />
                            </button>
                            {collaborationTemplateDropdownOpen && (
                              <div className="absolute z-50 mt-1 w-[360px] max-w-[calc(100vw-3rem)] max-h-72 overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-lg">
                                {isLoadingCollaborationTemplates ? (
                                  <div className="flex items-center justify-center gap-2 px-3 py-4 text-xs text-slate-400">
                                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                    加载模板...
                                  </div>
                                ) : collaborationTemplateList.length > 0 ? (
                                  collaborationTemplateList.map((template) => {
                                    const isActive =
                                      template.id === collaborationYamlTemplate;
                                    return (
                                      <button
                                        key={template.id}
                                        type="button"
                                        onClick={() =>
                                          handleCollaborationYamlTemplateChange(
                                            template.id,
                                          )
                                        }
                                        className={cn(
                                          'w-full px-3 py-2.5 text-left transition-colors hover:bg-slate-50',
                                          isActive && 'bg-lavender-50/70',
                                        )}
                                      >
                                        <div className="flex items-center gap-2 min-w-0">
                                          <span className="min-w-0 truncate text-sm font-medium text-slate-700">
                                            {template.name}
                                          </span>
                                          <div className="flex min-w-0 flex-1 items-center gap-1 overflow-hidden">
                                            {template.tags.map((tag) => (
                                              <span
                                                key={tag}
                                                className={cn(
                                                  'max-w-[76px] flex-shrink-0 truncate rounded-full border px-1.5 py-0.5 text-[10px] font-medium leading-none',
                                                  getCollaborationTagColorClass(
                                                    tag,
                                                  ),
                                                )}
                                              >
                                                {getCollaborationTagLabel(tag)}
                                              </span>
                                            ))}
                                          </div>
                                          {isActive && (
                                            <Check className="w-4 h-4 text-lavender-500 flex-shrink-0" />
                                          )}
                                        </div>
                                        {template.description && (
                                          <div className="mt-1.5 line-clamp-2 text-xs leading-5 text-slate-400">
                                            {template.description}
                                          </div>
                                        )}
                                      </button>
                                    );
                                  })
                                ) : (
                                  <div className="px-3 py-4 text-center text-xs text-slate-400">
                                    暂无后端模板
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        )}
                        {bcnCollaborationDocUrl && (
                          <a
                            href={bcnCollaborationDocUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1 text-xs font-medium text-lavender-600 hover:text-lavender-700 whitespace-nowrap"
                          >
                            <ExternalLink className="w-3.5 h-3.5" />
                            查看文档
                          </a>
                        )}
                      </div>
                      <div className="flex items-center gap-2 flex-shrink-0">
                        {participantDefinitions.length > 0 &&
                          !isCollaborationYamlValidated && (
                            <span className="text-xs px-2 py-0.5 rounded-full text-amber-600 bg-amber-50">
                              YAML 已变更，请重新校验
                            </span>
                          )}
                        <button
                          type="button"
                          onClick={() => void handleValidateCollaborationYaml()}
                          disabled={
                            isLoadingCollaborationYaml ||
                            isValidatingCollaborationYaml ||
                            !collaborationDefinitionYaml.trim()
                          }
                          className={cn(
                            'flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg transition-all border',
                            !isLoadingCollaborationYaml &&
                              !isValidatingCollaborationYaml &&
                              collaborationDefinitionYaml.trim()
                              ? 'bg-lavender-50 text-lavender-600 hover:bg-lavender-100 border-lavender-200'
                              : 'bg-slate-100 text-slate-400 cursor-not-allowed border-transparent',
                          )}
                        >
                          {isValidatingCollaborationYaml ? (
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          ) : (
                            <Check className="w-3.5 h-3.5" />
                          )}
                          {isValidatingCollaborationYaml
                            ? '校验中...'
                            : '校验 YAML'}
                        </button>
                      </div>
                    </div>
                    <div className="flex-1 min-h-0 border border-slate-200/60 rounded-xl overflow-hidden bg-white">
                      {isLoadingCollaborationYaml ? (
                        <div className="flex items-center justify-center h-full min-h-[240px] text-slate-400 gap-2">
                          <Loader2 className="w-4 h-4 animate-spin" />
                          <span className="text-sm">加载模板内容...</span>
                        </div>
                      ) : (
                        <React.Suspense
                          fallback={
                            <div className="flex items-center justify-center h-full min-h-[240px] text-slate-400 gap-2">
                              <Loader2 className="w-4 h-4 animate-spin" />
                              <span className="text-sm">加载编辑器...</span>
                            </div>
                          }
                        >
                          <LazyCodeMirror
                            value={collaborationDefinitionYaml}
                            height="100%"
                            placeholder={YAML_EDITOR_PLACEHOLDER}
                            extensions={yamlEditorExtensions}
                            onChange={handleCollaborationYamlChange}
                            basicSetup={{
                              lineNumbers: true,
                              foldGutter: true,
                              highlightActiveLine: true,
                              highlightActiveLineGutter: true,
                              bracketMatching: true,
                            }}
                            theme="light"
                            style={{
                              height: '100%',
                              fontSize: 13,
                            }}
                            className="h-full text-sm"
                          />
                        </React.Suspense>
                      )}
                    </div>
                  </div>
                )}

                {collaborationAuthoringStage === 'bindings' && (
                  <div className="flex-1 min-h-0 flex flex-col border border-slate-200/60 rounded-xl overflow-hidden bg-white">
                    <div className="flex items-center justify-between px-3 py-2 border-b border-slate-100 flex-shrink-0">
                      <div className="text-sm font-medium text-slate-700">
                        角色绑定
                      </div>
                      <div className="flex items-center gap-1.5">
                        {validatedCollaborationGraph &&
                          isCompactCollaborationFlowLayout && (
                            <Button
                              variant="secondary"
                              soft
                              size="sm"
                              leftIcon={<Workflow className="h-3.5 w-3.5" />}
                              onClick={() =>
                                setIsCompactCollaborationFlowOpen(true)
                              }
                            >
                              协作流程预览
                            </Button>
                          )}
                        {participantDefinitions.length > 0 &&
                          !isOriginatorBoundToParticipant && (
                            <span className="text-xs font-medium px-2 py-0.5 rounded-full text-amber-600 bg-amber-50">
                              发起方未绑定
                            </span>
                          )}
                        {boundParticipantCount > 0 && (
                          <span className="text-xs font-medium px-2 py-0.5 rounded-full text-lavender-600 bg-lavender-50">
                            已绑定 {boundParticipantCount} /{' '}
                            {participantDefinitions.length} 个角色，共{' '}
                            {allBoundBotIds.length} 个 Bot
                          </span>
                        )}
                      </div>
                    </div>

                    {participantDefinitions.length === 0 ? (
                      <div className="flex-1 min-h-0 flex items-center justify-center px-6 text-center text-sm text-slate-400">
                        校验 YAML 后在这里绑定 participants
                      </div>
                    ) : (
                      <>
                        <div className="p-2 border-b border-slate-100 flex-shrink-0">
                          <div className="relative">
                            <button
                              type="button"
                              aria-label="查看上一组角色"
                              title="上一组角色"
                              disabled={!canScrollParticipantTabsLeft}
                              onClick={() => scrollParticipantTabs('previous')}
                              className={cn(
                                'absolute left-0 inset-y-0 z-10 w-8 rounded-lg border flex items-center justify-center backdrop-blur-[1px] transition-colors',
                                canScrollParticipantTabsLeft
                                  ? 'bg-white/70 border-slate-200 text-slate-500 hover:bg-white/90 hover:text-slate-700'
                                  : 'bg-white border-slate-100 text-slate-300 cursor-not-allowed',
                              )}
                            >
                              <ChevronLeft className="w-4 h-4" />
                            </button>
                            <div
                              ref={participantTabsRef}
                              onScroll={updateParticipantTabsScrollState}
                              className="hide-scrollbar flex min-w-0 gap-1.5 overflow-x-auto overscroll-x-contain px-9"
                              role="group"
                              aria-label="协作角色"
                            >
                              {participantDefinitions.map((definition) => {
                                const boundCount = (
                                  participantBindings[definition.key] || []
                                ).slice(0, 1).length;
                                const isActive =
                                  definition.key === activeParticipantKey;
                                const needsBinding =
                                  (definition.required ||
                                    definition.assigned) &&
                                  boundCount === 0;
                                const requiresBinding =
                                  definition.required || definition.assigned;
                                return (
                                  <button
                                    key={definition.key}
                                    ref={(node) => {
                                      if (node) {
                                        participantTabButtonRefs.current.set(
                                          definition.key,
                                          node,
                                        );
                                      } else {
                                        participantTabButtonRefs.current.delete(
                                          definition.key,
                                        );
                                      }
                                    }}
                                    type="button"
                                    title={definition.name}
                                    aria-pressed={isActive}
                                    onClick={() => {
                                      setActiveParticipantKey(definition.key);
                                      setSelectedCollaborationNodeId(undefined);
                                    }}
                                    className={cn(
                                      'flex items-center gap-2 w-44 flex-shrink-0 px-2 py-1.5 rounded-lg border text-left transition-all',
                                      isActive
                                        ? 'border-lavender-300 bg-lavender-50'
                                        : needsBinding
                                        ? 'border-amber-200 bg-amber-50/40 hover:bg-amber-50'
                                        : 'border-slate-100 hover:bg-slate-50',
                                    )}
                                  >
                                    <span className="flex-1 min-w-0 truncate text-xs font-medium text-slate-700">
                                      {getCollaborationParticipantLabel(
                                        definition,
                                      )}
                                    </span>
                                    <span
                                      className={cn(
                                        'flex-shrink-0 text-[10px] px-1.5 py-0.5 rounded-full',
                                        requiresBinding
                                          ? needsBinding
                                            ? 'bg-amber-100 text-amber-700'
                                            : 'bg-green-50 text-green-600'
                                          : 'bg-slate-100 text-slate-500',
                                      )}
                                    >
                                      {boundCount > 0
                                        ? '已绑定'
                                        : requiresBinding
                                        ? '需绑定'
                                        : '可选'}
                                    </span>
                                  </button>
                                );
                              })}
                            </div>
                            <button
                              type="button"
                              aria-label="查看下一组角色"
                              title="下一组角色"
                              disabled={!canScrollParticipantTabsRight}
                              onClick={() => scrollParticipantTabs('next')}
                              className={cn(
                                'absolute right-0 inset-y-0 z-10 w-8 rounded-lg border flex items-center justify-center backdrop-blur-[1px] transition-colors',
                                canScrollParticipantTabsRight
                                  ? 'bg-white/70 border-slate-200 text-slate-500 hover:bg-white/90 hover:text-slate-700'
                                  : 'bg-white border-slate-100 text-slate-300 cursor-not-allowed',
                              )}
                            >
                              <ChevronRight className="w-4 h-4" />
                            </button>
                          </div>
                        </div>

                        <div className="flex-1 min-h-0 flex flex-col p-2">
                          {activeBindingBotIds.length > 0 && (
                            <div className="flex flex-wrap gap-1.5 mb-2 flex-shrink-0 max-h-16 overflow-y-auto">
                              {activeBindingBotIds.map((botId) => {
                                const bot = getBotInfo(botId);
                                return (
                                  <div
                                    key={botId}
                                    className={cn(
                                      'flex items-center gap-1.5 pl-1 pr-1.5 py-0.5 border rounded-full text-xs',
                                      bot?.dynamic_status?.status ===
                                        'offline' && !bot?.is_friend
                                        ? 'bg-amber-50 border-amber-200 text-amber-700'
                                        : 'bg-lavender-50 border-lavender-200 text-lavender-700',
                                    )}
                                  >
                                    <BotAvatar
                                      type="assistant"
                                      size="xs"
                                      name={getBotName(botId)}
                                      botId={botId?.split(':')[0]}
                                      avatarUrl={bot?.avatar_url}
                                      className="w-4 h-4 rounded-full"
                                    />
                                    <span className="max-w-[84px] truncate font-medium">
                                      {getBotName(botId)}
                                    </span>
                                    <button
                                      type="button"
                                      onClick={() => toggleBot(botId)}
                                      className="hover:opacity-70 transition-opacity flex-shrink-0"
                                    >
                                      <X className="w-3 h-3" />
                                    </button>
                                  </div>
                                );
                              })}
                            </div>
                          )}

                          <div className="flex items-center gap-2 mb-2 flex-shrink-0 min-w-0 overflow-x-auto">
                            <div className="flex items-center bg-slate-100 rounded-lg p-0.5 flex-shrink-0">
                              <button
                                type="button"
                                onClick={() => setMemberTab('friends')}
                                className={cn(
                                  'px-3 py-1 text-xs font-medium rounded-md transition-all',
                                  memberTab === 'friends'
                                    ? 'bg-white text-slate-800 shadow-sm'
                                    : 'text-slate-500 hover:text-slate-700',
                                )}
                              >
                                {actorKind === 'human'
                                  ? '我的 Bot'
                                  : '我的好友'}
                              </button>
                              <button
                                type="button"
                                onClick={() => setMemberTab('collaborate')}
                                className={cn(
                                  'px-3 py-1 text-xs font-medium rounded-md transition-all',
                                  memberTab === 'collaborate'
                                    ? 'bg-white text-slate-800 shadow-sm'
                                    : 'text-slate-500 hover:text-slate-700',
                                )}
                              >
                                可协作Bot
                              </button>
                            </div>

                            {memberTab === 'collaborate' && (
                              <div className="flex items-center gap-1.5 flex-shrink-0">
                                <ChevronRight className="w-3.5 h-3.5 text-slate-300" />
                                <span className="text-[11px] font-medium text-slate-400 whitespace-nowrap">
                                  筛选方式
                                </span>
                                <div className="flex items-center bg-slate-50 rounded-lg p-0.5 border border-slate-100">
                                  <button
                                    type="button"
                                    onClick={() => setFilterMode('name')}
                                    className={cn(
                                      'px-2.5 py-1 text-xs rounded-md transition-all whitespace-nowrap',
                                      filterMode === 'name'
                                        ? 'bg-white text-slate-800 shadow-sm'
                                        : 'text-slate-500 hover:text-slate-700',
                                    )}
                                  >
                                    按名称筛选
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => setFilterMode('profile')}
                                    className={cn(
                                      'px-2.5 py-1 text-xs rounded-md transition-all whitespace-nowrap',
                                      filterMode === 'profile'
                                        ? 'bg-white text-slate-800 shadow-sm'
                                        : 'text-slate-500 hover:text-slate-700',
                                    )}
                                  >
                                    按画像筛选
                                  </button>
                                </div>
                              </div>
                            )}
                          </div>

                          <div className="relative mb-2 flex-shrink-0">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
                            <input
                              type="text"
                              value={botSearch}
                              onChange={(e) => setBotSearch(e.target.value)}
                              onKeyDown={(e) => {
                                if (
                                  e.key === 'Enter' &&
                                  botSearch.trim() &&
                                  memberTab === 'collaborate' &&
                                  filterMode === 'profile'
                                ) {
                                  lastEnterSearchRef.current = botSearch;
                                  handleSmartSearch(botSearch);
                                  isFirstSmartSearch.current = false;
                                }
                              }}
                              placeholder={
                                memberTab === 'collaborate' &&
                                filterMode === 'profile'
                                  ? '输入关键词，智能匹配推荐 Bot'
                                  : '搜索 Bot 名称或描述...'
                              }
                              className="w-full pl-8 pr-3.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-lavender-500/20 focus:border-lavender-400 transition-all"
                            />
                            {((memberTab === 'collaborate' &&
                              filterMode === 'name' &&
                              isLoadingNameSearch) ||
                              (memberTab === 'collaborate' &&
                                filterMode === 'profile' &&
                                isLoadingSmartSearch)) && (
                              <div className="absolute right-3 top-1/2 -translate-y-1/2">
                                <Loader2 className="w-3.5 h-3.5 animate-spin text-lavender-500" />
                              </div>
                            )}
                          </div>

                          <div
                            ref={listRef}
                            onScroll={handleListScroll}
                            className="flex-1 min-h-[120px] border border-slate-200/60 rounded-xl overflow-auto scroll-pb-6"
                          >
                            {actorKind === 'bot' &&
                            (!originator?.is_online ||
                              originator?.dynamic_status?.status ===
                                'offline') ? (
                              <PrivateBotHint
                                visibility={originator?.visibility}
                                dynamicStatus={originator?.dynamic_status}
                              />
                            ) : isLoadingBots ? (
                              <div className="flex items-center justify-center h-full min-h-[160px] text-slate-400 gap-2">
                                <Loader2 className="w-4 h-4 animate-spin" />
                                <span className="text-sm">
                                  加载 Bot 列表...
                                </span>
                              </div>
                            ) : stateMachineFilteredBots.length === 0 ? (
                              <div className="flex items-center justify-center h-full min-h-[160px] text-sm text-slate-400">
                                {botSearch.trim()
                                  ? '未找到匹配的 Bot'
                                  : '暂无可选 Bot'}
                              </div>
                            ) : (
                              <div className="divide-y divide-slate-100 pb-6">
                                {stateMachineFilteredBots.map((bot, index) => {
                                  const isSelected =
                                    activeBindingBotIds.includes(bot.bot_uuid);
                                  const isAvailable = isBotOnline(bot);
                                  const unavailableTip =
                                    getUnavailableBotTip(bot);

                                  return !isAvailable ? (
                                    <TooltipProvider
                                      key={bot.bot_uuid}
                                      delayDuration={100}
                                    >
                                      <Tooltip>
                                        <TooltipTrigger asChild>
                                          <div className="w-full flex items-center gap-2.5 px-3 py-1 text-left transition-colors opacity-60 cursor-not-allowed">
                                            <BotAvatar
                                              type="assistant"
                                              size="sm"
                                              name={bot.bot_name}
                                              botId={
                                                bot.bot_uuid?.split(':')[0]
                                              }
                                              avatarUrl={bot.avatar_url}
                                            />
                                            <div className="flex-1 min-w-0">
                                              <p className="text-sm font-medium text-slate-800 truncate">
                                                {bot.bot_name}
                                              </p>
                                              {bot.summary && (
                                                <p className="text-xs text-slate-400 truncate leading-tight">
                                                  {bot.summary}
                                                </p>
                                              )}
                                            </div>
                                            <div className="w-5 h-5 rounded-full border border-slate-300 flex items-center justify-center flex-shrink-0">
                                              <Plus className="w-3 h-3 text-slate-400" />
                                            </div>
                                          </div>
                                        </TooltipTrigger>
                                        <TooltipContent side="top">
                                          <span>{unavailableTip}</span>
                                        </TooltipContent>
                                      </Tooltip>
                                    </TooltipProvider>
                                  ) : (
                                    <button
                                      key={bot.bot_uuid}
                                      type="button"
                                      onClick={() =>
                                        toggleBot(bot.bot_uuid, index)
                                      }
                                      className={cn(
                                        'w-full flex items-center gap-2.5 px-3 py-1 text-left transition-colors',
                                        isSelected
                                          ? 'bg-lavender-50/70'
                                          : 'hover:bg-slate-50',
                                      )}
                                    >
                                      <BotAvatar
                                        type="assistant"
                                        size="sm"
                                        name={bot.bot_name}
                                        botId={bot.bot_uuid?.split(':')[0]}
                                        avatarUrl={bot.avatar_url}
                                      />
                                      <div className="flex-1 min-w-0">
                                        <div className="flex items-center gap-2">
                                          <p className="text-sm font-medium text-slate-800 truncate">
                                            {bot.bot_name}
                                          </p>
                                          <GoldBadge botInfo={bot as any} />
                                          <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-green-50 text-green-600">
                                            在线
                                          </span>
                                          {bot.bot_uuid ===
                                            originator?.bot_uuid && (
                                            <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-lavender-50 text-lavender-600">
                                              发起方
                                            </span>
                                          )}
                                        </div>
                                        {bot.summary && (
                                          <p className="text-xs text-slate-400 truncate leading-tight">
                                            {bot.summary}
                                          </p>
                                        )}
                                      </div>
                                      <div
                                        className={cn(
                                          'w-5 h-5 rounded-full flex items-center justify-center flex-shrink-0 transition-all',
                                          isSelected
                                            ? 'bg-lavender-500 text-white'
                                            : 'border border-slate-300',
                                        )}
                                      >
                                        {isSelected ? (
                                          <Check className="w-3 h-3" />
                                        ) : (
                                          <Plus className="w-3 h-3 text-slate-400" />
                                        )}
                                      </div>
                                    </button>
                                  );
                                })}

                                {memberTab === 'collaborate' &&
                                  filterMode === 'name' &&
                                  isLoadingMoreBots && (
                                    <div className="flex items-center justify-center py-3 text-slate-400 gap-2">
                                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                      <span className="text-xs">
                                        加载更多...
                                      </span>
                                    </div>
                                  )}
                              </div>
                            )}
                          </div>
                        </div>
                      </>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <>
                {/* 标题行 */}
                <div className="flex items-center justify-between mb-2 flex-shrink-0">
                  <label className="text-sm font-medium text-slate-700">
                    成员 Bot
                  </label>
                  {consultantBots.length > 0 && (
                    <span
                      className={cn(
                        'text-xs font-medium px-2 py-0.5 rounded-full',
                        consultantBots.length >= MAX_MEMBER_BOTS
                          ? 'text-amber-600 bg-amber-50'
                          : 'text-lavender-600 bg-lavender-50',
                      )}
                    >
                      已选 {consultantBots.length}/{MAX_MEMBER_BOTS} 个
                    </span>
                  )}
                </div>

                {/* 已选 Bot chips */}
                {consultantBots.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mb-2 flex-shrink-0 max-h-20 overflow-y-auto">
                    {consultantBots.map((botId) => {
                      const bot = getBotInfo(botId);
                      return (
                        <div
                          key={botId}
                          className={cn(
                            'flex items-center gap-1.5 pl-1 pr-1.5 py-0.5 border rounded-full text-xs',
                            bot?.dynamic_status?.status === 'offline' &&
                              !bot?.is_friend
                              ? 'bg-amber-50 border-amber-200 text-amber-700'
                              : 'bg-lavender-50 border-lavender-200 text-lavender-700',
                          )}
                        >
                          <BotAvatar
                            type="assistant"
                            size="xs"
                            name={getBotName(botId)}
                            botId={botId?.split(':')[0]}
                            avatarUrl={bot?.avatar_url}
                            className="w-4 h-4 rounded-full"
                          />
                          <span className="max-w-[72px] truncate font-medium">
                            {getBotName(botId)}
                          </span>
                          {bot?.dynamic_status?.status === 'offline' &&
                            !bot?.is_friend && (
                              <Lock className="w-3 h-3 text-amber-500" />
                            )}
                          <button
                            type="button"
                            // position 会从 currentBotsRef 中查找
                            onClick={() => toggleBot(botId)}
                            className="hover:opacity-70 transition-opacity flex-shrink-0"
                            data-aspm-click="ca114903.da194175"
                            data-aspm-desc="GroupChat-移除已选Bot"
                            data-aspm-param={``}
                            data-aspm-expo
                          >
                            <X className="w-3 h-3" />
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}

                {/* 一层 Tab：我的好友 / 可协作Bot + 智能推荐按钮 */}
                <div className="flex items-center justify-between mb-2 flex-shrink-0">
                  <div className="flex items-center bg-slate-100 rounded-lg p-0.5">
                    <button
                      type="button"
                      onClick={() => setMemberTab('friends')}
                      className={cn(
                        'px-4 py-1.5 text-xs font-medium rounded-md transition-all',
                        memberTab === 'friends'
                          ? 'bg-white text-slate-800 shadow-sm'
                          : 'text-slate-500 hover:text-slate-700',
                      )}
                      data-aspm-click="ca114903.da194176"
                      data-aspm-desc="GroupChat-切换我的好友Tab"
                      data-aspm-param={``}
                      data-aspm-expo
                    >
                      {actorKind === 'human' ? '我的 Bot' : '我的好友'}
                    </button>
                    <button
                      type="button"
                      onClick={() => setMemberTab('collaborate')}
                      className={cn(
                        'px-4 py-1.5 text-xs font-medium rounded-md transition-all',
                        memberTab === 'collaborate'
                          ? 'bg-white text-slate-800 shadow-sm'
                          : 'text-slate-500 hover:text-slate-700',
                      )}
                      data-aspm-click="ca114903.da194177"
                      data-aspm-desc="GroupChat-切换可协作BotTab"
                      data-aspm-param={``}
                      data-aspm-expo
                    >
                      可协作Bot
                    </button>
                  </div>
                  {/* 智能推荐按钮（始终显示） */}
                  <button
                    type="button"
                    onClick={handleSmartRecommend}
                    disabled={
                      !(description.trim() || topic.trim()) || isRecommending
                    }
                    className={cn(
                      'flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg transition-all',
                      (description.trim() || topic.trim()) && !isRecommending
                        ? 'bg-lavender-50 text-lavender-600 hover:bg-lavender-100 border border-lavender-200'
                        : 'bg-slate-100 text-slate-400 cursor-not-allowed border border-transparent',
                    )}
                    title={
                      !(description.trim() || topic.trim())
                        ? '请先输入协作目标或协作群名称'
                        : '根据协作目标智能推荐 Bot'
                    }
                    data-aspm-click="ca114903.da194178"
                    data-aspm-desc="GroupChat-智能推荐Bot"
                    data-aspm-param={``}
                    data-aspm-expo
                  >
                    {isRecommending ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <Sparkles className="w-3.5 h-3.5" />
                    )}
                    {isRecommending ? '推荐中...' : '智能推荐'}
                  </button>
                </div>

                {/* 二层 Tab + 搜索框（仅在可协作Bot下显示，且在同一行） */}
                {memberTab === 'collaborate' ? (
                  <div className="flex items-center gap-2 mb-2 flex-shrink-0">
                    {/* 二层 Tab：按名称筛选 / 按画像筛选 */}
                    <div className="flex items-center bg-slate-50 rounded-lg p-0.5 border border-slate-100 flex-shrink-0">
                      <button
                        type="button"
                        onClick={() => setFilterMode('name')}
                        className={cn(
                          'px-3 py-1 text-xs rounded-md transition-all whitespace-nowrap',
                          filterMode === 'name'
                            ? 'bg-white text-slate-800 shadow-sm'
                            : 'text-slate-500 hover:text-slate-700',
                        )}
                        data-aspm-click="ca114903.da194179"
                        data-aspm-desc="GroupChat-按名称筛选"
                        data-aspm-param={``}
                        data-aspm-expo
                      >
                        按名称筛选
                      </button>
                      <button
                        type="button"
                        onClick={() => setFilterMode('profile')}
                        className={cn(
                          'px-3 py-1 text-xs rounded-md transition-all whitespace-nowrap',
                          filterMode === 'profile'
                            ? 'bg-white text-slate-800 shadow-sm'
                            : 'text-slate-500 hover:text-slate-700',
                        )}
                        data-aspm-click="ca114903.da194180"
                        data-aspm-desc="GroupChat-按画像筛选"
                        data-aspm-param={``}
                        data-aspm-expo
                      >
                        按画像筛选
                      </button>
                    </div>
                    {/* 搜索框 */}
                    <div className="relative flex-1">
                      <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
                      <input
                        type="text"
                        value={botSearch}
                        onChange={(e) => setBotSearch(e.target.value)}
                        onKeyDown={(e) => {
                          if (
                            e.key === 'Enter' &&
                            botSearch.trim() &&
                            filterMode === 'profile'
                          ) {
                            // 记录回车搜索内容，防抖会跳过相同内容的请求
                            lastEnterSearchRef.current = botSearch;
                            handleSmartSearch(botSearch);
                            // 回车后不再是首次搜索
                            isFirstSmartSearch.current = false;
                          }
                        }}
                        placeholder={
                          filterMode === 'name'
                            ? '搜索 Bot 名称或描述...'
                            : '输入关键词，智能匹配推荐 Bot，按回车可立即搜索'
                        }
                        className="w-full pl-8 pr-3.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-lavender-500/20 focus:border-lavender-400 transition-all"
                      />
                      {filterMode === 'profile' && isLoadingSmartSearch && (
                        <div className="absolute right-3 top-1/2 -translate-y-1/2">
                          <Loader2 className="w-3.5 h-3.5 animate-spin text-lavender-500" />
                        </div>
                      )}
                      {filterMode === 'name' && isLoadingNameSearch && (
                        <div className="absolute right-3 top-1/2 -translate-y-1/2">
                          <Loader2 className="w-3.5 h-3.5 animate-spin text-lavender-500" />
                        </div>
                      )}
                    </div>
                  </div>
                ) : (
                  /* 我的好友 tab 下的搜索框 */
                  <div className="relative mb-2 flex-shrink-0">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
                    <input
                      type="text"
                      value={botSearch}
                      onChange={(e) => setBotSearch(e.target.value)}
                      placeholder="搜索 Bot 名称或描述..."
                      className="w-full pl-8 pr-3.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-lavender-500/20 focus:border-lavender-400 transition-all"
                    />
                  </div>
                )}

                {/* Bot 列表（flex-1 撑满，内部滚动） */}
                <div
                  ref={listRef}
                  onScroll={handleListScroll}
                  className="flex-1 min-h-0 border border-slate-200/60 rounded-xl overflow-auto"
                >
                  {actorKind === 'bot' &&
                  (!originator?.is_online ||
                    originator?.dynamic_status?.status === 'offline') ? (
                    <PrivateBotHint
                      visibility={originator?.visibility}
                      dynamicStatus={originator?.dynamic_status}
                    />
                  ) : isLoadingBots ? (
                    <div className="flex items-center justify-center h-full min-h-[200px] text-slate-400 gap-2">
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <span className="text-sm">
                        {botSearch.trim()
                          ? memberTab === 'collaborate' && filterMode === 'name'
                            ? '搜索中...'
                            : '智能搜索中...'
                          : '加载 Bot 列表...'}
                      </span>
                    </div>
                  ) : filteredBots.length === 0 ? (
                    <div className="flex items-center justify-center h-full min-h-[200px] text-sm text-slate-400">
                      {botSearch.trim() ? '未找到匹配的 Bot' : '暂无可选 Bot'}
                    </div>
                  ) : (
                    <div className="divide-y divide-slate-100">
                      {filteredBots.map((bot, index) => {
                        const isSelected = consultantBots.includes(
                          bot.bot_uuid,
                        );
                        const isAvailable = isBotOnline(bot);
                        // 无论哪个tab，只有可用Bot才能选择；已达上限时不可再选
                        const canInvite =
                          isAvailable &&
                          (isSelected ||
                            consultantBots.length < MAX_MEMBER_BOTS);
                        const unavailableTip = isAvailable
                          ? `最多选择 ${MAX_MEMBER_BOTS} 个成员 Bot`
                          : getUnavailableBotTip(bot);

                        return !canInvite ? (
                          <TooltipProvider delayDuration={100}>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <div
                                  className={cn(
                                    'w-full flex items-center gap-2.5 px-3 py-1 text-left transition-colors opacity-60 cursor-not-allowed',
                                  )}
                                >
                                  <BotAvatar
                                    type="assistant"
                                    size="sm"
                                    name={bot.bot_name}
                                    botId={bot.bot_uuid?.split(':')[0]}
                                    avatarUrl={bot.avatar_url}
                                  />
                                  <div className="flex-1 min-w-0">
                                    <div className="flex items-center gap-2">
                                      <p className="text-sm font-medium text-slate-800 truncate">
                                        {bot.bot_name}
                                      </p>
                                      <GoldBadge botInfo={bot as any} />
                                      {/* 好友标签 */}
                                      {memberTab === 'collaborate' &&
                                        bot.is_friend && (
                                          <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-lavender-50 text-lavender-600">
                                            好友
                                          </span>
                                        )}
                                    </div>
                                    {bot.summary && (
                                      <p className="text-xs text-slate-400 truncate leading-tight">
                                        {bot.summary}
                                      </p>
                                    )}
                                  </div>
                                  <div className="w-5 h-5 rounded-full border border-slate-300 flex items-center justify-center flex-shrink-0">
                                    <Plus className="w-3 h-3 text-slate-400" />
                                  </div>
                                </div>
                              </TooltipTrigger>
                              <TooltipContent side="top">
                                <span>{unavailableTip}</span>
                              </TooltipContent>
                            </Tooltip>
                          </TooltipProvider>
                        ) : (
                          <button
                            key={bot.bot_uuid}
                            type="button"
                            onClick={() => toggleBot(bot.bot_uuid, index)}
                            className={cn(
                              'w-full flex items-center gap-2.5 px-3 py-1 text-left transition-colors',
                              isSelected
                                ? 'bg-lavender-50/70'
                                : 'hover:bg-slate-50',
                            )}
                            data-aspm-click="ca114903.da194181"
                            data-aspm-desc="GroupChat-选择Bot"
                            data-aspm-param={``}
                            data-aspm-expo
                          >
                            <BotAvatar
                              type="assistant"
                              size="sm"
                              name={bot.bot_name}
                              botId={bot.bot_uuid?.split(':')[0]}
                              avatarUrl={bot.avatar_url}
                            />
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <p className="text-sm font-medium text-slate-800 truncate">
                                  {bot.bot_name}
                                </p>
                                <GoldBadge botInfo={bot as any} />
                                {/* 在线状态 - 两个tab都显示 */}
                                <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-green-50 text-green-600">
                                  在线
                                </span>
                                {memberTab === 'collaborate' &&
                                  bot.is_friend && (
                                    <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-lavender-50 text-lavender-600">
                                      好友
                                    </span>
                                  )}
                              </div>
                              {bot.summary && (
                                <p className="text-xs text-slate-400 truncate leading-tight">
                                  {bot.summary}
                                </p>
                              )}
                              {(bot as any).short_profile && (
                                <TooltipProvider delayDuration={100}>
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <div className="flex items-center gap-1 mt-0.5 text-xs text-blue-500 bg-blue-50/50 px-1.5 py-0.5 rounded w-fit cursor-pointer">
                                        <Sparkles className="w-3 h-3" />
                                        推荐理由：
                                        <span className="truncate">
                                          {(bot as any).short_profile}
                                        </span>
                                      </div>
                                    </TooltipTrigger>
                                    <TooltipContent
                                      side="top"
                                      className="bg-slate-800 border-slate-700 max-w-xs"
                                    >
                                      <div className="flex items-start gap-1">
                                        <Sparkles className="w-3 h-3 text-amber-400 mt-0.5 flex-shrink-0" />
                                        <span className="text-white">
                                          推荐理由：
                                          {(bot as any).short_profile}
                                        </span>
                                      </div>
                                    </TooltipContent>
                                  </Tooltip>
                                </TooltipProvider>
                              )}
                            </div>
                            <div
                              className={cn(
                                'w-5 h-5 rounded-full flex items-center justify-center flex-shrink-0 transition-all',
                                isSelected
                                  ? 'bg-lavender-500 text-white'
                                  : 'border border-slate-300',
                              )}
                            >
                              {isSelected ? (
                                <Check className="w-3 h-3" />
                              ) : (
                                <Plus className="w-3 h-3 text-slate-400" />
                              )}
                            </div>
                          </button>
                        );
                      })}

                      {/* 加载更多指示器（仅按名称筛选模式下） */}
                      {memberTab === 'collaborate' &&
                        filterMode === 'name' &&
                        isLoadingMoreBots && (
                          <div className="flex items-center justify-center py-3 text-slate-400 gap-2">
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                            <span className="text-xs">加载更多...</span>
                          </div>
                        )}
                      {memberTab === 'collaborate' &&
                        filterMode === 'name' &&
                        !hasMoreAvailableBots &&
                        filteredBots.length > 0 &&
                        !botSearch && (
                          <div className="py-2.5 text-center text-xs text-slate-300">
                            已加载全部 {filteredBots.length} 个 Bot
                          </div>
                        )}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </CollaborationFlowWorkspace>

        {/* 底部：错误 + 按钮 */}
        <div className="flex items-center justify-between px-6 py-2 border-t border-slate-100 flex-shrink-0">
          <div className="flex-1">
            {error && <p className="text-sm text-red-500">{error}</p>}
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <Button
              variant="default"
              ghost
              onClick={handleClose}
              data-aspm-click="ca114903.da194182"
              data-aspm-desc="GroupChat-取消创建协作"
              data-aspm-param={``}
              data-aspm-expo
            >
              取消
            </Button>
            <Button
              variant="primary"
              onClick={handleCreate}
              loading={isCreatingGroup}
              disabled={
                isCreatingGroup ||
                !originator ||
                (groupStrategy === 'state_machine'
                  ? !description.trim() ||
                    isCollaborationTemplateLoading ||
                    isValidatingCollaborationYaml ||
                    !collaborationDefinitionYaml.trim() ||
                    !isCollaborationYamlValidated ||
                    missingParticipantBindingLabels.length > 0 ||
                    allBoundBotIds.length === 0 ||
                    !isOriginatorBoundToParticipant
                  : false) ||
                (groupStrategy === 'chat' && !driverBotId) ||
                (groupStrategy === 'manager_worker' && !masterBot)
              }
              data-aspm-click="ca114903.da194183"
              data-aspm-desc="GroupChat-确认创建协作"
              data-aspm-param={``}
              data-aspm-expo
            >
              创建协作群
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default CreateGroupModal;
