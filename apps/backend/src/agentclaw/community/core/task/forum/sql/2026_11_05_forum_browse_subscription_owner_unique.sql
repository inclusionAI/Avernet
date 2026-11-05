-- 2026-11-05: Browse subscriptions must be scoped by owner.
--
-- ``default`` is a shared Bot identity: each owner addresses it as
-- ``default:<owner_user_id>``. The previous unique key allowed only one row per
-- bare ``bot_id``, so the first owner's switch state leaked to every other
-- owner. Keep the index name and widen it with ``owner_user_id``.

ALTER TABLE `ac_forum_browse_subscription`
    DROP INDEX `uk_forum_browse_sub_bot`;

ALTER TABLE `ac_forum_browse_subscription`
    ADD UNIQUE KEY `uk_forum_browse_sub_bot`
    (`avernet_tenant`, `env`, `bot_id`, `owner_user_id`);
