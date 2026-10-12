/**
 * BBS 实验反馈通道领域类型（Avernet BBS contract §5.2）。
 * 反馈归属通用模块，不限定 BBS；前端以 module='bbs' 提交。
 */

/** 后端 FeedbackCreated 原始（{id:int}）。 */
export interface FeedbackCreatedDto {
  id: number;
}

/** 提交反馈入参（contract §5.2.1）。 */
export interface CreateFeedbackInput {
  /** 反馈人标识（人工工号），调用方声明。 */
  reporterId: string;
  /** 反馈所属模块，固定 'bbs'。 */
  module: string;
  /** 反馈内容，1–4000 字。 */
  content: string;
}

export type { FeedbackCreatedDto as FeedbackItem };
