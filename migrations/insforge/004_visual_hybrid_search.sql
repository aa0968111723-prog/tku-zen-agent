-- 004_visual_hybrid_search.sql
--
-- 這支函式**已經存在於正式資料庫**，卻不在本 repo 的任何 migration 裡。
-- 它是 2026-08-26 那次整備時透過 InsForge 管理 API 直接套用的，因此 repo 的
-- migrations/ 從來就不是遠端 schema 的完整真相（見 docs/GROK_REPAIR_STATUS.json）。
--
-- 本檔是 2026-08-27 從正式庫 pg_get_functiondef() 取回的逐字快照，補上這段落差，
-- 讓 repo 具備可重建性。因為用的是 CREATE OR REPLACE，對現行環境重跑是無害的。
--
-- 對應的呼叫端：config.INSFORGE_SEARCH_RPC 預設 "visual_hybrid_search"
--   （app/config.py:212）→ app/services/insforge_adapters.py:411 → app/visual_api.py:349
-- 回傳欄位 asset_id / score / match_reason 正是該呼叫端期待的形狀。

CREATE OR REPLACE FUNCTION public.visual_hybrid_search(p_query text DEFAULT ''::text, p_filters jsonb DEFAULT '{}'::jsonb, p_owner_id text DEFAULT ''::text, p_page integer DEFAULT 1, p_limit integer DEFAULT 30, p_query_embedding vector DEFAULT NULL::vector)
 RETURNS TABLE(asset_id text, id text, score real, lexical_score real, semantic_score real, match_reason text, reason text)
 LANGUAGE sql
 STABLE
AS $function$
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
$function$

;
