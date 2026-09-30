-- Persist optional editable cold-open proposals for generated clips.
-- Values are absolute source-video timestamps in seconds.

ALTER TABLE generated_clips
    ADD COLUMN IF NOT EXISTS cold_open_start FLOAT,
    ADD COLUMN IF NOT EXISTS cold_open_end FLOAT;
