-- ac_forum_interaction 从事件流水转为互斥动作状态表（2026-10-28）。
--
-- `active` 保存累计有效量：`0` 表示失效，`>0` 参与 summary 统计。重复浏览允许累加为 `2`、`3`。
-- 唯一键包含用户指定的 actor_type、actor_id、action_type、target_type、target_id，
-- 并额外包含 env，避免不同环境隔离分区中的相同动作互相冲突。
--
-- 旧数据如果已存在重复动作，保留 id 最小的最早记录；后续业务通过同一行更新
-- active 数值，不再追加重复事件。

ALTER TABLE `ac_forum_interaction`
    ADD COLUMN `active` int NOT NULL DEFAULT 1
        COMMENT '互动累计有效量：>0参与计数，0无效' AFTER `env`;

DELETE `duplicate_row` FROM `ac_forum_interaction` AS `duplicate_row`
JOIN `ac_forum_interaction` AS `earliest_row`
    ON `earliest_row`.`env` = `duplicate_row`.`env`
   AND `earliest_row`.`actor_type` = `duplicate_row`.`actor_type`
   AND `earliest_row`.`actor_id` = `duplicate_row`.`actor_id`
   AND `earliest_row`.`action_type` = `duplicate_row`.`action_type`
   AND `earliest_row`.`target_type` = `duplicate_row`.`target_type`
   AND `earliest_row`.`target_id` = `duplicate_row`.`target_id`
WHERE `earliest_row`.`id` < `duplicate_row`.`id`;

ALTER TABLE `ac_forum_interaction`
    ADD UNIQUE KEY `uk_forum_interaction_action`
        (`env`, `actor_type`, `actor_id`, `action_type`, `target_type`, `target_id`);
