-- Upgrade already-created ``ac_forum_interaction.active`` columns from the boolean
-- storage representation (tinyint(1)) to an integer accumulated amount.
--
-- Request ``active`` is a +1/-1 delta; the persisted value is this row's
-- accumulated amount. Zero is inactive, and positive values contribute to
-- summaries. Repeated VIEW requests may therefore persist values greater than 1.

ALTER TABLE `ac_forum_interaction`
    MODIFY COLUMN `active` int NOT NULL DEFAULT 1
        COMMENT '互动累计有效量：>0参与计数，0无效';
