-- InsForge/PostgreSQL migration for the optional visual backend.
-- Apply through the InsForge migration runner.  The local SQLite migration
-- 0003_insforge_visual_backend keeps an equivalent mapping for fallback.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS sources (
  id text PRIMARY KEY,
  source_type text NOT NULL DEFAULT '',
  source_url text NOT NULL DEFAULT '',
  original_file text NOT NULL DEFAULT '',
  captured_at timestamptz,
  reliability_score real NOT NULL DEFAULT 0,
  owner_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS entities (
  id text PRIMARY KEY,
  type text NOT NULL CHECK (type IN ('person','club','school','event','scene','place','object')),
  name text NOT NULL,
  aliases jsonb NOT NULL DEFAULT '[]'::jsonb,
  description text NOT NULL DEFAULT '',
  confidence real NOT NULL DEFAULT 0 CHECK (confidence >= 0 AND confidence <= 1),
  verification_status text NOT NULL DEFAULT 'pending_review',
  owner_id text NOT NULL,
  source_id text REFERENCES sources(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS visual_assets (
  id text PRIMARY KEY,
  project_id text NOT NULL DEFAULT '',
  owner_id text NOT NULL,
  storage_path text NOT NULL,
  thumbnail_path text NOT NULL DEFAULT '',
  mime_type text NOT NULL,
  width integer NOT NULL DEFAULT 0,
  height integer NOT NULL DEFAULT 0,
  duration real NOT NULL DEFAULT 0,
  sha256 text NOT NULL,
  exif_date timestamptz,
  source text NOT NULL DEFAULT '',
  permission_status text NOT NULL DEFAULT 'private',
  analysis_status text NOT NULL DEFAULT 'pending_review',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(owner_id, sha256)
);

CREATE TABLE IF NOT EXISTS asset_entities (
  asset_id text NOT NULL REFERENCES visual_assets(id) ON DELETE CASCADE,
  entity_id text NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  relation_type text NOT NULL,
  confidence real NOT NULL DEFAULT 0,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  verified_by text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(asset_id, entity_id, relation_type)
);

CREATE TABLE IF NOT EXISTS events (
  id text PRIMARY KEY,
  name text NOT NULL,
  date date,
  time time,
  location text NOT NULL DEFAULT '',
  club_id text REFERENCES entities(id),
  school_id text REFERENCES entities(id),
  verification_status text NOT NULL DEFAULT 'pending_review',
  owner_id text NOT NULL,
  source_id text REFERENCES sources(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS review_queue (
  id text PRIMARY KEY,
  asset_id text NOT NULL REFERENCES visual_assets(id) ON DELETE CASCADE,
  proposed_change jsonb NOT NULL DEFAULT '{}'::jsonb,
  confidence real NOT NULL DEFAULT 0,
  status text NOT NULL DEFAULT 'pending_review',
  reviewer_id text NOT NULL DEFAULT '',
  reviewed_at timestamptz,
  owner_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS embeddings (
  id text PRIMARY KEY,
  asset_id text NOT NULL REFERENCES visual_assets(id) ON DELETE CASCADE,
  text_embedding vector(1536),
  image_embedding vector(1536),
  model text NOT NULL,
  owner_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(asset_id, model)
);

CREATE TABLE IF NOT EXISTS sync_runs (
  id text PRIMARY KEY,
  source text NOT NULL DEFAULT 'insforge',
  owner_id text NOT NULL,
  project_id text NOT NULL DEFAULT '',
  manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'pending',
  imported_count integer NOT NULL DEFAULT 0,
  failed_count integer NOT NULL DEFAULT 0,
  resumed_from text NOT NULL DEFAULT '',
  error_log jsonb NOT NULL DEFAULT '[]'::jsonb,
  rollback_status text NOT NULL DEFAULT 'not_requested',
  idempotency_key text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(owner_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_visual_assets_owner_created ON visual_assets(owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_visual_assets_sha256 ON visual_assets(sha256);
CREATE INDEX IF NOT EXISTS idx_asset_entities_entity ON asset_entities(entity_id, asset_id);
CREATE INDEX IF NOT EXISTS idx_review_queue_owner_status ON review_queue(owner_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_embeddings_image ON embeddings USING ivfflat (image_embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_embeddings_text ON embeddings USING ivfflat (text_embedding vector_cosine_ops) WITH (lists = 100);

ALTER TABLE visual_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE asset_entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE sync_runs ENABLE ROW LEVEL SECURITY;

-- InsForge exposes auth.uid() in PostgREST requests.  Service-role jobs may
-- bypass RLS server-side; browser clients never receive the service key.
DO $$
BEGIN
  EXECUTE 'CREATE POLICY visual_assets_owner ON visual_assets USING (owner_id = auth.uid()::text) WITH CHECK (owner_id = auth.uid()::text)';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$
BEGIN
  EXECUTE 'CREATE POLICY entities_owner ON entities USING (owner_id = auth.uid()::text) WITH CHECK (owner_id = auth.uid()::text)';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$
BEGIN
  EXECUTE 'CREATE POLICY asset_entities_owner ON asset_entities USING (EXISTS (SELECT 1 FROM visual_assets a WHERE a.id = asset_id AND a.owner_id = auth.uid()::text)) WITH CHECK (EXISTS (SELECT 1 FROM visual_assets a WHERE a.id = asset_id AND a.owner_id = auth.uid()::text))';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$
BEGIN
  EXECUTE 'CREATE POLICY review_queue_owner ON review_queue USING (owner_id = auth.uid()::text) WITH CHECK (owner_id = auth.uid()::text)';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$
BEGIN
  EXECUTE 'CREATE POLICY embeddings_owner ON embeddings USING (owner_id = auth.uid()::text) WITH CHECK (owner_id = auth.uid()::text)';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$
BEGIN
  EXECUTE 'CREATE POLICY sync_runs_owner ON sync_runs USING (owner_id = auth.uid()::text) WITH CHECK (owner_id = auth.uid()::text)';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
