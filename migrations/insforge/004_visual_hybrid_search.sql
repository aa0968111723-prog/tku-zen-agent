-- Additive visual search contract the FastAPI adapter already calls.
-- Apply after 001_visual_backend.sql, 002_core_data_layer.sql and
-- 003_data_organization_depth.sql. No BEGIN/COMMIT/ROLLBACK: the InsForge
-- migration runner wraps this file in its own transaction.

ALTER TABLE sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE events ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  EXECUTE 'CREATE POLICY sources_owner ON sources USING (owner_id = auth.uid()::text) WITH CHECK (owner_id = auth.uid()::text)';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$
BEGIN
  EXECUTE 'CREATE POLICY events_owner ON events USING (owner_id = auth.uid()::text) WITH CHECK (owner_id = auth.uid()::text)';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_visual_assets_source_fts
  ON visual_assets USING gin (to_tsvector('simple', coalesce(source, '') || ' ' || coalesce(storage_path, '')));
CREATE INDEX IF NOT EXISTS idx_entities_name_fts
  ON entities USING gin (to_tsvector('simple', coalesce(name, '')));
CREATE INDEX IF NOT EXISTS idx_entities_owner_type_name
  ON entities(owner_id, type, name);

CREATE OR REPLACE FUNCTION visual_hybrid_search(
  p_query text DEFAULT '',
  p_filters jsonb DEFAULT '{}'::jsonb,
  p_owner_id text DEFAULT '',
  p_page integer DEFAULT 1,
  p_limit integer DEFAULT 30,
  p_query_embedding vector(1536) DEFAULT NULL
)
RETURNS TABLE (
  asset_id text,
  id text,
  score real,
  lexical_score real,
  semantic_score real,
  match_reason text,
  reason text
)
LANGUAGE sql
STABLE
SECURITY INVOKER
AS $$
  WITH f AS (
    SELECT COALESCE(p_filters, '{}'::jsonb) AS filters
  ),
  scoped AS (
    SELECT
      a.id,
      a.source,
      a.storage_path,
      a.permission_status,
      a.analysis_status,
      a.created_at,
      COALESCE((
        SELECT string_agg(e.name, ' ')
        FROM asset_entities ae
        JOIN entities e ON e.id = ae.entity_id
        WHERE ae.asset_id = a.id
      ), '') AS entity_names
    FROM visual_assets a
    CROSS JOIN f
    WHERE a.owner_id = p_owner_id
      AND p_owner_id <> ''
      AND (auth.uid() IS NULL OR a.owner_id = auth.uid()::text)
      AND (
        COALESCE(f.filters->>'privacy', '') = ''
        OR a.permission_status = f.filters->>'privacy'
      )
      AND (
        COALESCE(f.filters->>'verification_status', '') = ''
        OR a.analysis_status = f.filters->>'verification_status'
      )
      AND (
        COALESCE(f.filters->>'school', '') = ''
        OR EXISTS (
          SELECT 1
          FROM asset_entities ae
          JOIN entities e ON e.id = ae.entity_id
          WHERE ae.asset_id = a.id
            AND e.type = 'school'
            AND e.name ILIKE '%' || (f.filters->>'school') || '%'
        )
      )
      AND (
        COALESCE(f.filters->>'club', '') = ''
        OR EXISTS (
          SELECT 1
          FROM asset_entities ae
          JOIN entities e ON e.id = ae.entity_id
          WHERE ae.asset_id = a.id
            AND e.type = 'club'
            AND e.name ILIKE '%' || (f.filters->>'club') || '%'
        )
      )
      AND (
        COALESCE(f.filters->>'person', '') = ''
        OR EXISTS (
          SELECT 1
          FROM asset_entities ae
          JOIN entities e ON e.id = ae.entity_id
          WHERE ae.asset_id = a.id
            AND e.type = 'person'
            AND e.verification_status = 'verified'
            AND e.name ILIKE '%' || (f.filters->>'person') || '%'
        )
      )
      AND (
        COALESCE(f.filters->>'scene', '') = ''
        OR EXISTS (
          SELECT 1
          FROM asset_entities ae
          JOIN entities e ON e.id = ae.entity_id
          WHERE ae.asset_id = a.id
            AND e.type = 'scene'
            AND e.name ILIKE '%' || (f.filters->>'scene') || '%'
        )
      )
      AND (
        COALESCE(f.filters->>'event', '') = ''
        OR EXISTS (
          SELECT 1
          FROM asset_entities ae
          JOIN entities e ON e.id = ae.entity_id
          WHERE ae.asset_id = a.id
            AND e.type = 'event'
            AND e.name ILIKE '%' || (f.filters->>'event') || '%'
        )
      )
  ),
  scored AS (
    SELECT
      s.id,
      CASE
        WHEN COALESCE(p_query, '') = '' THEN 0::real
        ELSE GREATEST(
          ts_rank_cd(
            to_tsvector('simple', s.source || ' ' || s.storage_path || ' ' || s.entity_names),
            plainto_tsquery('simple', p_query)
          ),
          CASE
            WHEN s.source ILIKE '%' || p_query || '%'
              OR s.storage_path ILIKE '%' || p_query || '%'
              OR s.entity_names ILIKE '%' || p_query || '%'
            THEN 0.15
            ELSE 0
          END
        )::real
      END AS lexical_score,
      CASE
        WHEN p_query_embedding IS NULL OR emb.text_embedding IS NULL THEN 0::real
        ELSE (1 - (emb.text_embedding <=> p_query_embedding))::real
      END AS semantic_score
    FROM scoped s
    LEFT JOIN LATERAL (
      SELECT e.text_embedding
      FROM embeddings e
      WHERE e.asset_id = s.id AND e.owner_id = p_owner_id
      ORDER BY e.created_at DESC
      LIMIT 1
    ) emb ON TRUE
    WHERE
      COALESCE(p_query, '') = ''
      OR to_tsvector('simple', s.source || ' ' || s.storage_path || ' ' || s.entity_names)
           @@ plainto_tsquery('simple', p_query)
      OR s.source ILIKE '%' || p_query || '%'
      OR s.storage_path ILIKE '%' || p_query || '%'
      OR s.entity_names ILIKE '%' || p_query || '%'
      OR (p_query_embedding IS NOT NULL AND emb.text_embedding IS NOT NULL)
  )
  SELECT
    scored.id AS asset_id,
    scored.id AS id,
    (scored.lexical_score * 0.55 + scored.semantic_score * 0.45)::real AS score,
    scored.lexical_score,
    scored.semantic_score,
    CASE
      WHEN scored.lexical_score > 0 AND scored.semantic_score > 0.12 THEN 'keyword + semantic'
      WHEN scored.lexical_score > 0 THEN 'keyword'
      WHEN scored.semantic_score > 0.12 THEN 'semantic'
      ELSE 'owner-scoped filter'
    END AS match_reason,
    CASE
      WHEN scored.lexical_score > 0 AND scored.semantic_score > 0.12 THEN 'keyword + semantic'
      WHEN scored.lexical_score > 0 THEN 'keyword'
      WHEN scored.semantic_score > 0.12 THEN 'semantic'
      ELSE 'owner-scoped filter'
    END AS reason
  FROM scored
  ORDER BY (scored.lexical_score * 0.55 + scored.semantic_score * 0.45) DESC, scored.id
  LIMIT LEAST(GREATEST(COALESCE(p_limit, 30), 1), 100)
  OFFSET GREATEST(
    (GREATEST(COALESCE(p_page, 1), 1) - 1) * LEAST(GREATEST(COALESCE(p_limit, 30), 1), 100),
    0
  );
$$;
