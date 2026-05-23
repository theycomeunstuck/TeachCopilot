-- db/schema.sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS children (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(100) NOT NULL,
    age_group   VARCHAR(20),
    grade       VARCHAR(20),
    language    VARCHAR(20) DEFAULT 'russian',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS child_profiles (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    child_id            UUID NOT NULL REFERENCES children(id) ON DELETE CASCADE,
    explanation_style   TEXT,
    pace                VARCHAR(50),
    autonomy_level      VARCHAR(50),
    motivation          VARCHAR(50),
    prefers_visual      BOOLEAN DEFAULT FALSE,
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS child_knowledge (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    child_id    UUID NOT NULL REFERENCES children(id) ON DELETE CASCADE,
    subject     VARCHAR(100),
    topic       VARCHAR(200),
    status      VARCHAR(50),    -- 'knows' | 'learning' | 'struggling'
    score       SMALLINT CHECK (score BETWEEN 0 AND 100),
    notes       TEXT,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT child_knowledge_child_subject_topic_uq
        UNIQUE (child_id, subject, topic)
);

CREATE TABLE IF NOT EXISTS knowledge_base (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject             VARCHAR(100),
    topic               VARCHAR(200),
    content             TEXT NOT NULL,
    image_descriptions  TEXT,        -- Phase 2: vision model output for images on page
    source_file         VARCHAR(500),
    page_number         INTEGER,     -- Phase 2: PDF page number
    image_path          VARCHAR(500), -- Phase 2: path to saved page image PNG
    embedding           vector(384),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT knowledge_base_source_topic_uq
        UNIQUE (source_file, topic),
    CONSTRAINT knowledge_base_source_page_uq
        UNIQUE (source_file, page_number)
);

-- Child interests (tags for personalization)
CREATE TABLE IF NOT EXISTS child_interests (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    child_id    UUID NOT NULL REFERENCES children(id) ON DELETE CASCADE,
    tag         VARCHAR(100) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT child_interests_child_tag_uq UNIQUE (child_id, tag)
);

-- Error patterns (recurring mistakes)
CREATE TABLE IF NOT EXISTS error_patterns (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    child_id    UUID NOT NULL REFERENCES children(id) ON DELETE CASCADE,
    subject     VARCHAR(100),
    topic       VARCHAR(200),
    error_tag   VARCHAR(100) NOT NULL,
    count       INTEGER DEFAULT 1,
    last_seen   TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT error_patterns_child_subj_tag_uq UNIQUE (child_id, subject, error_tag)
);

-- Maps Open WebUI user_id to child_id for speaker detection
CREATE TABLE IF NOT EXISTS user_mappings (
    user_id     TEXT PRIMARY KEY,
    child_id    UUID REFERENCES children(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Event log for analytics, profile updates, debugging
CREATE TABLE IF NOT EXISTS events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    child_id    UUID REFERENCES children(id) ON DELETE CASCADE,
    event_type  VARCHAR(100),
    payload     JSONB,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS knowledge_base_embedding_idx
    ON knowledge_base USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS events_child_id_idx
    ON events (child_id, created_at DESC);
