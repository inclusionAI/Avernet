// useAdmin：空间列表搜索（防抖）/ 类型筛选 / 分页 / 创建团队空间 / 打开详情拉成员 /
// 申请加入。编排 store + adminService，错误 toast（经统一 notify 入口）。成员增删改（含批量添加）
// 下沉到 useSpaceMemberActions，避免本 Hook 超文件体积阈值（Hook ≤ 250 行）。
import { notifyError, notifySuccess } from '@/components/ui/notify';
import type { Space } from '@/domain/admin/models';
import { sortSpacesByDisplayOrder } from '@/domain/spaceContext';
import { useSpaceMemberActions } from '@/hooks/useSpaceMemberActions';
import { adminService } from '@/services/admin';
import { useAdminStore } from '@/stores/adminStore';
import { shouldMuteNonAuthedToast } from '@/utils/loginToastGate';
import { useCallback, useEffect, useRef } from 'react';
import { toast } from 'sonner';

const SEARCH_DEBOUNCE_MS = 300;

export function useAdmin() {
  const {
    keyword,
    spaceType,
    pageNo,
    pageSize,
    items,
    total,
    loading,
    error,
    currentSpace,
    members,
    membersLoading,
    setKeyword,
    setPageNo,
    setPageSize,
    setList,
    setLoading,
    setError,
    setCurrentSpace,
    setMembers,
    setMembersLoading,
  } = useAdminStore();
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const keywordRef = useRef(keyword);
  // 成员增删改（含批量添加 Promise.allSettled 聚合）编排下沉到此子 Hook。
  const memberActions = useSpaceMemberActions({ currentSpace, setMembers });

  const fetchList = useCallback(
    async (override?: { keyword?: string; spaceType?: typeof spaceType; pageNo?: number; pageSize?: number }) => {
      const kw = override?.keyword ?? keywordRef.current;
      const st = override?.spaceType ?? spaceType;
      const p = override?.pageNo ?? pageNo;
      const ps = override?.pageSize ?? pageSize;
      setLoading(true);
      setError(null);
      const r = await adminService.listSpaces({
        keyword: kw,
        spaceType: st === 'ALL' ? undefined : st,
        page: p,
        pageSize: ps,
      });
      setLoading(false);
      if (r.error) {
        setError(r.error);
        // 未登录（oauth-provider + 非 authenticated）静默：自动加载 toast 统一由 ExternalLoginPromptModal 承担。
        if (!shouldMuteNonAuthedToast()) notifyError(r.error.message, { requestId: r.error.requestId });
        return;
      }
      setList(sortSpacesByDisplayOrder(r.data?.items ?? []), r.data?.total ?? 0);
    },
    [spaceType, pageNo, pageSize, setLoading, setError, setList],
  );

  // keyword 变化防抖触发
  const onKeywordChange = useCallback(
    (kw: string) => {
      setKeyword(kw);
      keywordRef.current = kw;
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => void fetchList({ keyword: kw, pageNo: 1 }), SEARCH_DEBOUNCE_MS);
    },
    [fetchList, setKeyword],
  );

  // 防抖清理
  useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    },
    [],
  );

  const changePage = useCallback(
    (p: number) => {
      setPageNo(p);
      void fetchList({ pageNo: p });
    },
    [fetchList, setPageNo],
  );

  // 切换每页条数：store 内同时重置回第 1 页，显式传 pageSize 避免闭包旧值
  const changePageSize = useCallback(
    (size: number) => {
      setPageSize(size);
      void fetchList({ pageSize: size, pageNo: 1 });
    },
    [fetchList, setPageSize],
  );

  const createTeamSpace = useCallback(
    async (spaceName: string) => {
      const r = await adminService.createTeamSpace({ spaceName });
      if (r.error) {
        notifyError(r.error.message, { title: '创建团队失败', requestId: r.error.requestId });
        return false;
      }
      notifySuccess('空间创建成功');
      void fetchList({ pageNo: 1 });
      return true;
    },
    [fetchList],
  );

  const openSpaceDetail = useCallback(
    async (space: Space) => {
      // 权限闸：仅「已加入」的空间可打开成员抽屉；未加入则提示用户并直接拦截，不 set currentSpace、不拉成员。
      const access = adminService.canViewMembers(space);
      if (!access.ok) {
        toast.warning(access.reason ?? '暂无权限查看该空间成员列表');
        return;
      }
      setCurrentSpace(space);
      if (!space.spaceId) return;
      setMembersLoading(true);
      const r = await adminService.listMembers(space.spaceId);
      setMembersLoading(false);
      if (r.error) {
        notifyError(r.error.message, { title: '加载成员失败', requestId: r.error.requestId });
        return;
      }
      setMembers(r.data?.items ?? []);
    },
    [setCurrentSpace, setMembers, setMembersLoading],
  );

  const closeSpaceDetail = useCallback(() => setCurrentSpace(null), [setCurrentSpace]);

  const deleteSpace = useCallback(
    async (spaceId: number | string) => {
      const r = await adminService.deleteSpace(spaceId);
      if (r.error) {
        notifyError(r.error.message, { title: '删除空间失败', requestId: r.error.requestId });
        return false;
      }
      notifySuccess('空间已删除');
      closeSpaceDetail();
      void fetchList({ pageNo: 1 });
      return true;
    },
    [closeSpaceDetail, fetchList],
  );

  const requestJoin = useCallback(
    async (spaceId: number | string, reason: string) => {
      const r = await adminService.requestJoin(spaceId, reason);
      if (r.error) {
        notifyError(r.error.message, { title: '申请加入失败', requestId: r.error.requestId });
        return;
      }
      notifySuccess('已提交申请，等待审批');
      void fetchList({ pageNo: 1 });
    },
    [fetchList],
  );

  // 首次挂载拉取（仅当未加载过）
  const bootedRef = useRef(false);
  useEffect(() => {
    if (bootedRef.current) return;
    bootedRef.current = true;
    void fetchList();
  }, [fetchList]);

  return {
    keyword,
    spaceType,
    pageNo,
    pageSize,
    items,
    total,
    loading,
    error,
    currentSpace,
    members,
    membersLoading,
    onKeywordChange,
    changePage,
    changePageSize,
    createTeamSpace,
    openSpaceDetail,
    closeSpaceDetail,
    deleteSpace,
    addMember: memberActions.addMember,
    addMembers: memberActions.addMembers,
    addMembersLoading: memberActions.addMembersLoading,
    addMembersDisabledReason: memberActions.addMembersDisabledReason,
    removeMember: memberActions.removeMember,
    updateRole: memberActions.updateRole,
    requestJoin,
    fetchList,
  };
}
