// useAdmin：空间列表搜索（防抖）/ 类型筛选 / 分页 / 创建团队空间 / 打开详情拉成员 /
// 成员增删改 / 申请加入。编排 store + adminService，错误 toast（经统一 notify 入口）。
import { notifyError, notifySuccess } from '@/components/ui/notify';
import type { Space } from '@/domain/admin/models';
import { sortSpacesByDisplayOrder } from '@/domain/spaceContext';
import { adminService } from '@/services/admin';
import { useAdminStore } from '@/stores/adminStore';
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
        notifyError(r.error.message, { requestId: r.error.requestId });
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

  const refreshMembers = useCallback(async () => {
    if (!currentSpace?.spaceId) return;
    const r = await adminService.listMembers(currentSpace.spaceId);
    if (!r.error) setMembers(r.data?.items ?? []);
  }, [currentSpace, setMembers]);

  const addMember = useCallback(
    async (userId: string, role: 'ADMIN' | 'MEMBER' = 'MEMBER', userName?: string) => {
      if (!currentSpace?.spaceId) return;
      const r = await adminService.addMember(currentSpace.spaceId, userId, role, userName);
      if (r.error) {
        notifyError(r.error.message, { title: '添加成员失败', requestId: r.error.requestId });
        return;
      }
      notifySuccess('成员已添加');
      void refreshMembers();
    },
    [currentSpace, refreshMembers],
  );

  const removeMember = useCallback(
    async (userId: string) => {
      if (!currentSpace?.spaceId) return;
      const r = await adminService.removeMember(currentSpace.spaceId, userId);
      if (r.error) {
        notifyError(r.error.message, { title: '移除成员失败', requestId: r.error.requestId });
        return;
      }
      notifySuccess('成员已移除');
      void refreshMembers();
    },
    [currentSpace, refreshMembers],
  );

  const updateRole = useCallback(
    async (userId: string, role: 'ADMIN' | 'MEMBER') => {
      if (!currentSpace?.spaceId) return;
      const r = await adminService.updateRole(currentSpace.spaceId, userId, role);
      if (r.error) {
        notifyError(r.error.message, { title: '修改角色失败', requestId: r.error.requestId });
        return;
      }
      notifySuccess('角色已更新');
      void refreshMembers();
    },
    [currentSpace, refreshMembers],
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
    addMember,
    removeMember,
    updateRole,
    requestJoin,
    fetchList,
  };
}
