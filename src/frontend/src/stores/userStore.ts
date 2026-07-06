/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * User Store - 全局用户信息管理
 */

import type { UserInfo } from '@/services/backend-api/UserController';
import { sendAuthToken } from '@/utils/vscodeMessenger';
import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

interface UserState {
  // State - 当前登录用户
  userId: string;
  nickName: string;
  avatarUrl?: string;
  accessToken?: string;
  iamToken?: string;

  // State - 查询用户列表
  users: UserInfo[];
  isLoadingUsers: boolean;

  // Actions - 当前登录用户
  setUserId: (userId: string) => void;
  setNickName: (nickName: string) => void;
  setAvatarUrl: (avatarUrl?: string) => void;
  setAccessToken: (token: string) => void;
  setIamToken: (token: string) => void;

  // Actions - 查询用户列表
  setUsers: (users: UserInfo[]) => void;
  setLoadingUsers: (loading: boolean) => void;

  reset: () => void;
}

const initialState = {
  userId: '',
  nickName: '',
  avatarUrl: undefined,
  accessToken: undefined,
  iamToken: undefined,
  users: [],
  isLoadingUsers: false,
};

export const useUserStore = create<UserState>()(
  devtools(
    (set) => ({
      // Initial State
      ...initialState,

      // Actions - 当前登录用户
      setUserId: (userId) => set({ userId }, false, 'setUserId'),

      setNickName: (nickName) => set({ nickName }, false, 'setNickName'),

      setAvatarUrl: (avatarUrl) => set({ avatarUrl }, false, 'setAvatarUrl'),

      setAccessToken: (token) => {
        set({ accessToken: token }, false, 'setAccessToken');
        // Token 变化时通知 VSCode
        sendAuthToken(token);
      },

      setIamToken: (iamToken) => set({ iamToken }, false, 'setIamToken'),

      // Actions - 查询用户列表
      setUsers: (users) => set({ users }, false, 'user/setUsers'),

      setLoadingUsers: (isLoadingUsers) =>
        set({ isLoadingUsers }, false, 'user/setLoadingUsers'),

      reset: () => set(initialState, false, 'reset'),
    }),
    { name: 'UserStore' },
  ),
);
