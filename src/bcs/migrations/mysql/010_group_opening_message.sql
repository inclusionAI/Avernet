ALTER TABLE `bcs_groups`
  ADD COLUMN `opening_message_json` text DEFAULT NULL COMMENT '自定义协作群开场消息配置 JSON';
