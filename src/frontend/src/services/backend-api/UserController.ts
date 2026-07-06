/* eslint-disable */
/**
 * 用户管理 API
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import { getExt } from '@/capabilities';
import { AppExt } from '@/shell/extension';

/** 用户目录搜索结果 */
export interface AntbuUserInfo {
  chosenName: string;
  cnl: string;
  displayName: string;
  domainName: string;
  email: string;
  id: string;
  nickName: string;
  realName: string;
  staffNo: string;
  type: string;
  userId: string;
  userType: string;
}

/** 用户目录搜索响应 */
export interface AntbuUserSearchResponse {
  list: AntbuUserInfo[];
  stat: string;
  success: boolean;
}

/** 用户信息 */
export interface UserInfo {
  user_id: string; // = staffNo 100000
  // 用户目录原始字段映射（同名）
  realName: string; // 示例姓名
  nickName?: string; // 示例花名
  email?: string; // user@example.com
  chosenName?: string; // 示例姓名(user)
  displayName?: string; // 示例姓名(示例花名)user
  domainName?: string; // user
}

/**
 * 将 AntbuUserInfo 转换为 UserInfo
 */
export function convertAntbuToUserInfo(antbuUser: AntbuUserInfo): UserInfo {
  return {
    user_id: antbuUser.staffNo,
    // 保留原始用户目录字段（同名映射）
    realName: antbuUser.realName,
    nickName: antbuUser.nickName,
    email: antbuUser.email,
    chosenName: antbuUser.chosenName,
    displayName: antbuUser.displayName,
    domainName: antbuUser.domainName,
  };
}

/**
 * 批量获取用户信息（通过工号列表）
 * 委托用户目录适配器逐个查询
 * @param userIds 用户工号列表
 */
export async function getUsersByIds(
  userIds: string[],
  options?: Record<string, unknown>,
): Promise<UserInfo[]> {
  return getExt(AppExt).userDirectory.getUsersByIds(userIds, options);
}

/**
 * 搜索用户信息（花名/工号/邮箱）
 * @param keyword 搜索关键词
 * @param excludeSelf 是否排除自己，默认 true
 */
export async function searchUsersByAntbu(
  keyword: string,
  options?: Record<string, unknown>,
): Promise<AntbuUserInfo[]> {
  return getExt(AppExt).userDirectory.searchUsers(keyword, options);
}
