/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * Mock 集中开关
 *
 * 后端联调时改这一处即可，避免多个文件单独维护各自的 USE_MOCK 而漏改。
 *
 * 约定：消费方从这里 import，禁止再在文件内定义本地 USE_MOCK 常量。
 */

/** 服务 Bot 协作（协作者 / 锁 / 只读规则）是否走 Mock */
export const USE_SERVICE_BOT_COLLAB_MOCK = false;
