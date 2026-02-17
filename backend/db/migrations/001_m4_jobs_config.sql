
-- -----------------------------------------------------------
-- Migration: Add generated config columns to jobs table
-- -----------------------------------------------------------
ALTER TABLE jobs ADD COLUMN generated_config TEXT;
ALTER TABLE jobs ADD COLUMN rollback_config TEXT;
