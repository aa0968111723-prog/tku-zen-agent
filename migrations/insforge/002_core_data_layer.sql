-- Optional InsForge core data layer for project/artifact/activity/knowledge sync.
-- Apply after 001_visual_backend.sql. Local chat, memory, caches and secrets
-- are intentionally outside this migration.

CREATE TABLE IF NOT EXISTS projects (
  id text PRIMARY KEY,
  owner_id text NOT NULL,
  name text NOT NULL,
  task_type text NOT NULL DEFAULT '',
  summary text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
  id text PRIMARY KEY,
  owner_id text NOT NULL,
  project_id text REFERENCES projects(id) ON DELETE SET NULL,
  session_ref text NOT NULL DEFAULT '',
  filename text NOT NULL,
  kind text NOT NULL DEFAULT '',
  storage_path text NOT NULL DEFAULT '',
  drive_url text NOT NULL DEFAULT '',
  sha256 text NOT NULL DEFAULT '',
  size_bytes bigint NOT NULL DEFAULT 0,
  version integer NOT NULL DEFAULT 1,
  parent_id text REFERENCES artifacts(id) ON DELETE SET NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS activities (
  id text PRIMARY KEY,
  owner_id text NOT NULL,
  project_id text REFERENCES projects(id) ON DELETE SET NULL,
  name text NOT NULL,
  activity_type text NOT NULL DEFAULT '',
  academic_year text NOT NULL DEFAULT '',
  semester text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'planning',
  start_at text NOT NULL DEFAULT '',
  end_at text NOT NULL DEFAULT '',
  location text NOT NULL DEFAULT '',
  goal text NOT NULL DEFAULT '',
  audience text NOT NULL DEFAULT '',
  signup_url text NOT NULL DEFAULT '',
  activity_owner text NOT NULL DEFAULT '',
  notes text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_tasks (
  id text PRIMARY KEY,
  owner_id text NOT NULL,
  activity_id text NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
  title text NOT NULL,
  group_name text NOT NULL DEFAULT '',
  assignee text NOT NULL DEFAULT '',
  due_at text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'pending',
  priority text NOT NULL DEFAULT 'normal',
  notes text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS research_sources (
  id text PRIMARY KEY,
  owner_id text NOT NULL,
  project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  session_ref text NOT NULL DEFAULT '',
  title text NOT NULL DEFAULT '',
  url text NOT NULL DEFAULT '',
  source_date text NOT NULL DEFAULT '',
  summary text NOT NULL DEFAULT '',
  credibility real NOT NULL DEFAULT 0,
  verification text NOT NULL DEFAULT 'needs_verification',
  source_type text NOT NULL DEFAULT 'external_reference',
  source_file text NOT NULL DEFAULT '',
  entity_id text NOT NULL DEFAULT '',
  school text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_documents (
  id text PRIMARY KEY,
  owner_id text NOT NULL,
  project_id text NOT NULL DEFAULT '',
  title text NOT NULL,
  relative_path text NOT NULL,
  storage_path text NOT NULL DEFAULT '',
  source_type text NOT NULL DEFAULT 'archive',
  source_scope text NOT NULL DEFAULT 'internal',
  sha256 text NOT NULL,
  size_bytes bigint NOT NULL DEFAULT 0,
  modified_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(owner_id, relative_path, sha256)
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
  id text PRIMARY KEY,
  document_id text NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
  owner_id text NOT NULL,
  project_id text NOT NULL DEFAULT '',
  chunk_index integer NOT NULL,
  heading text NOT NULL DEFAULT '',
  content text NOT NULL,
  source_label text NOT NULL DEFAULT '',
  source_type text NOT NULL DEFAULT 'archive',
  source_scope text NOT NULL DEFAULT 'internal',
  school text NOT NULL DEFAULT '',
  entity_id text NOT NULL DEFAULT '',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  embedding vector(1536),
  embedding_model text NOT NULL DEFAULT '',
  content_sha256 text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(document_id, chunk_index, content_sha256)
);

CREATE INDEX IF NOT EXISTS idx_projects_owner_updated ON projects(owner_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_artifacts_owner_project ON artifacts(owner_id, project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activities_owner_project ON activities(owner_id, project_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_tasks_owner_activity ON activity_tasks(owner_id, activity_id, status);
CREATE INDEX IF NOT EXISTS idx_research_sources_owner_project ON research_sources(owner_id, project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_documents_owner_path ON knowledge_documents(owner_id, relative_path);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_fts ON knowledge_chunks USING gin (to_tsvector('simple', content || ' ' || heading));
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_vector ON knowledge_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE activities ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_chunks ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['projects','artifacts','activities','activity_tasks','research_sources','knowledge_documents','knowledge_chunks']
  LOOP
    BEGIN
      EXECUTE format(
        'CREATE POLICY %I_owner ON %I USING (owner_id = auth.uid()::text) WITH CHECK (owner_id = auth.uid()::text)',
        table_name, table_name
      );
    EXCEPTION WHEN duplicate_object THEN NULL;
    END;
  END LOOP;
END $$;

CREATE OR REPLACE FUNCTION knowledge_hybrid_search(
  p_owner_id text,
  p_query text,
  p_query_embedding vector(1536) DEFAULT NULL,
  p_project_id text DEFAULT '',
  p_limit integer DEFAULT 10
)
RETURNS TABLE (
  chunk_id text,
  document_id text,
  heading text,
  content text,
  source_label text,
  source_type text,
  lexical_score real,
  semantic_score real,
  score real
)
LANGUAGE sql STABLE SECURITY INVOKER AS $$
  WITH ranked AS (
    SELECT
      k.id AS chunk_id,
      k.document_id,
      k.heading,
      k.content,
      k.source_label,
      k.source_type,
      ts_rank_cd(to_tsvector('simple', k.content || ' ' || k.heading), plainto_tsquery('simple', p_query))::real AS lexical_score,
      CASE WHEN p_query_embedding IS NULL OR k.embedding IS NULL THEN 0::real
           ELSE (1 - (k.embedding <=> p_query_embedding))::real END AS semantic_score
    FROM knowledge_chunks k
    WHERE k.owner_id = p_owner_id
      AND (p_project_id = '' OR k.project_id = p_project_id)
      AND (p_query = '' OR to_tsvector('simple', k.content || ' ' || k.heading) @@ plainto_tsquery('simple', p_query)
           OR (p_query_embedding IS NOT NULL AND k.embedding IS NOT NULL))
  )
  SELECT
    ranked.chunk_id,
    ranked.document_id,
    ranked.heading,
    ranked.content,
    ranked.source_label,
    ranked.source_type,
    ranked.lexical_score,
    ranked.semantic_score,
    (ranked.lexical_score * 0.55 + ranked.semantic_score * 0.45)::real AS score
  FROM ranked
  ORDER BY (ranked.lexical_score * 0.55 + ranked.semantic_score * 0.45) DESC, ranked.chunk_id
  LIMIT LEAST(GREATEST(p_limit, 1), 50);
$$;
