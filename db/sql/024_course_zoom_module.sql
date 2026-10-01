-- Zoom как origin источников; привязка записи ко всему модулю без урока.

ALTER TABLE course_sources DROP CONSTRAINT IF EXISTS course_sources_origin_check;
ALTER TABLE course_sources ADD CONSTRAINT course_sources_origin_check
    CHECK (origin IN ('disk', 'youtube', 'vimeo', 'kinescope', 'zoom'));

ALTER TABLE course_sources ADD COLUMN IF NOT EXISTS module_no INT;
