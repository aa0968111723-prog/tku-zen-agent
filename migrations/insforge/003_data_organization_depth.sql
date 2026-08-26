-- Additive data organization, lineage, graph and context migration.
-- Apply after 001_visual_backend.sql and 002_core_data_layer.sql.

ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS logical_key text NOT NULL DEFAULT '';
ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS is_current boolean NOT NULL DEFAULT true;
ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS superseded_at timestamptz;

UPDATE knowledge_documents
SET logical_key = md5(owner_id || ':' || project_id || ':' || relative_path)
WHERE logical_key = '';

ALTER TABLE knowledge_documents
  DROP CONSTRAINT IF EXISTS knowledge_documents_owner_id_relative_path_sha256_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_document_version
  ON knowledge_documents(owner_id, project_id, relative_path, sha256);
CREATE INDEX IF NOT EXISTS idx_knowledge_document_current
  ON knowledge_documents(owner_id, project_id, logical_key, is_current);

WITH ranked AS (
  SELECT id, row_number() OVER (
    PARTITION BY owner_id, project_id, logical_key
    ORDER BY updated_at DESC, created_at DESC, id DESC
  ) AS version_rank
  FROM knowledge_documents
)
UPDATE knowledge_documents d
SET is_current = (ranked.version_rank = 1),
    superseded_at = CASE WHEN ranked.version_rank = 1 THEN NULL ELSE COALESCE(d.superseded_at, now()) END
FROM ranked
WHERE ranked.id = d.id;

CREATE TABLE IF NOT EXISTS data_inventory_reports (
  id text PRIMARY KEY,
  owner_id text NOT NULL,
  project_id text NOT NULL DEFAULT '',
  statistics jsonb NOT NULL DEFAULT '{}'::jsonb,
  breakdown jsonb NOT NULL DEFAULT '{}'::jsonb,
  manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'verified',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS data_lineage (
  resource_type text NOT NULL,
  resource_id text NOT NULL,
  owner_id text NOT NULL,
  original_id text NOT NULL DEFAULT '',
  original_path text NOT NULL DEFAULT '',
  original_source text NOT NULL DEFAULT '',
  source_id text NOT NULL DEFAULT '',
  sha256 text NOT NULL DEFAULT '',
  migration_version text NOT NULL DEFAULT '0005_data_organization_depth',
  confidence real NOT NULL DEFAULT 0,
  verification_status text NOT NULL DEFAULT 'pending_review',
  imported_at timestamptz NOT NULL DEFAULT now(),
  source_updated_at text NOT NULL DEFAULT '',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY(resource_type, resource_id, owner_id)
);

CREATE TABLE IF NOT EXISTS entity_relationships (
  id text PRIMARY KEY,
  owner_id text NOT NULL,
  from_type text NOT NULL,
  from_id text NOT NULL,
  to_type text NOT NULL,
  to_id text NOT NULL,
  relation_type text NOT NULL,
  confidence real NOT NULL DEFAULT 0,
  verification_status text NOT NULL DEFAULT 'pending_review',
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  source_id text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(owner_id, from_type, from_id, to_type, to_id, relation_type)
);

CREATE TABLE IF NOT EXISTS project_context_nodes (
  id text PRIMARY KEY,
  owner_id text NOT NULL,
  project_id text NOT NULL,
  parent_id text NOT NULL DEFAULT '',
  node_type text NOT NULL,
  title text NOT NULL,
  position integer NOT NULL DEFAULT 0,
  requirements jsonb NOT NULL DEFAULT '{}'::jsonb,
  verification_status text NOT NULL DEFAULT 'pending_review',
  source_id text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(owner_id, project_id, node_type, parent_id, position, title)
);

CREATE TABLE IF NOT EXISTS project_asset_links (
  context_node_id text NOT NULL,
  asset_id text NOT NULL,
  owner_id text NOT NULL,
  match_score real NOT NULL DEFAULT 0,
  reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
  verification_status text NOT NULL DEFAULT 'pending_review',
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(context_node_id, asset_id, owner_id)
);

CREATE INDEX IF NOT EXISTS idx_inventory_reports_owner ON data_inventory_reports(owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_data_lineage_source ON data_lineage(owner_id, source_id, verification_status);
CREATE INDEX IF NOT EXISTS idx_data_lineage_sha ON data_lineage(owner_id, sha256) WHERE sha256!='';
CREATE INDEX IF NOT EXISTS idx_entity_graph_from ON entity_relationships(owner_id, from_type, from_id);
CREATE INDEX IF NOT EXISTS idx_entity_graph_to ON entity_relationships(owner_id, to_type, to_id);
CREATE INDEX IF NOT EXISTS idx_project_context_order ON project_context_nodes(owner_id, project_id, node_type, position);
CREATE INDEX IF NOT EXISTS idx_project_asset_links_asset ON project_asset_links(owner_id, asset_id, match_score DESC);

ALTER TABLE data_inventory_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_lineage ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_relationships ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_context_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_asset_links ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['data_inventory_reports','data_lineage','entity_relationships','project_context_nodes','project_asset_links']
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
    JOIN knowledge_documents d ON d.id=k.document_id AND d.is_current=true
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
