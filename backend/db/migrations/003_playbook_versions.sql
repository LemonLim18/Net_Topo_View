-- ----------------------------------------------------------------
-- Migration 003: Add playbook_versions column to jobs table
-- (M25 Phase-2 — playbook refine/history)
-- ----------------------------------------------------------------

-- Store each playbook version as a JSON array:
-- [{"version": 1, "playbook": "...", "created_at": "...", "refinement_notes": ""}, ...]
ALTER TABLE jobs ADD COLUMN playbook_versions TEXT;
