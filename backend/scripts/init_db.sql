-- Extensions required by the Medix schema.
-- See docs/24-database.md.

CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- fuzzy product / batch search
CREATE EXTENSION IF NOT EXISTS btree_gin;    -- composite GIN with scalar columns
CREATE EXTENSION IF NOT EXISTS pgcrypto;     -- gen_random_uuid, field encryption
