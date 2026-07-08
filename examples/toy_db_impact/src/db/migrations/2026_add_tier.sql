-- migrations/2026_add_tier.sql
-- 故意有坑：增加了 plan_tier VARCHAR NOT NULL 却没有 DEFAULT 值！
ALTER TABLE subscriptions ADD COLUMN plan_tier VARCHAR NOT NULL;
