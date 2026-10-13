-- Read-only canonical audit. Bind :as_of as a UTC timestamp without time zone.
-- Run in REPEATABLE READ, READ ONLY alongside the priority plan for one snapshot.
WITH clock AS (SELECT CAST(:as_of AS timestamp) AS ts), tracked AS (
 SELECT c.*,w.last_checked_at,w.last_success_at,w.next_check_at,w.published_date
 FROM car_listings c LEFT JOIN website_listing_states w USING(listing_id)
 WHERE c.source='olx.ba' AND c.collection_method IN ('olx_api','olx_website')
), checks AS (
 SELECT outcome,count(*) AS attempts,count(distinct listing_id) AS distinct_ads,
 count(*) FILTER(WHERE checked_at >= (SELECT ts FROM clock)-interval '7 days') AS attempts_7d
 FROM website_listing_checks WHERE checked_at<=(SELECT ts FROM clock) GROUP BY outcome
), runs AS (
 SELECT id,started_at,finished_at,status,source_query,search_limit,detail_limit,
 search_attempted,detail_attempted,discovery_complete,details_completed,observations,new_listings,errors,
 report::jsonb->'page_stats' AS page_stats,report::jsonb->'source_total' AS source_reported_total,
 report::jsonb->'known_overlap' AS known_database_overlap,
 report::jsonb->'rejected' AS rejected,report::jsonb->'stopped_reason' AS stopped_reason
 FROM website_collection_runs WHERE started_at<=(SELECT ts FROM clock)
 ORDER BY started_at DESC,id DESC
)
SELECT json_build_object(
 'as_of_utc',(SELECT ts FROM clock),
 'summary',(SELECT json_build_object(
   'listings_all_methods',(SELECT count(*) FROM car_listings),
   'observations_all_methods',(SELECT count(*) FROM listing_snapshots),
   'tracked_nonlegacy',count(*),
   'last_observed_24h',count(*) FILTER(WHERE last_verified_at >= (SELECT ts FROM clock)-interval '1 day'),
   'last_observed_7d',count(*) FILTER(WHERE last_verified_at >= (SELECT ts FROM clock)-interval '7 days'),
   'last_observed_30d',count(*) FILTER(WHERE last_verified_at >= (SELECT ts FROM clock)-interval '30 days'),
   'observation_older_30d',count(*) FILTER(WHERE last_verified_at < (SELECT ts FROM clock)-interval '30 days'),
   'observation_age_unknown',count(*) FILTER(WHERE last_verified_at IS NULL),
   'never_directly_attempted',count(*) FILTER(WHERE last_checked_at IS NULL),
   'never_successfully_directly_checked',count(*) FILTER(WHERE last_success_at IS NULL),
   'successful_direct_7d',count(*) FILTER(WHERE last_success_at >= (SELECT ts FROM clock)-interval '7 days'),
   'due_for_direct_check',count(*) FILTER(WHERE next_check_at IS NULL OR next_check_at<=(SELECT ts FROM clock)),
   'first_seen_today',count(*) FILTER(WHERE first_seen_at >= date_trunc('day',(SELECT ts FROM clock))),
   'first_seen_7d',count(*) FILTER(WHERE first_seen_at >= (SELECT ts FROM clock)-interval '7 days'),
   'stored_posted_date',count(posted_date),'direct_page_published_date',count(published_date),
   'oldest_observation',min(last_verified_at),'latest_observation',max(last_verified_at),
   'clean_collection_anchor',(SELECT max(end_time) FROM scraping_logs WHERE status='completed' AND errors_count=0),
   'saved_search_count',(SELECT count(*) FROM dealer_model_searches),
   'candidate_count',(SELECT count(*) FROM dealer_candidates)
 ) FROM tracked),
 'check_outcomes',coalesce((SELECT json_agg(checks ORDER BY outcome) FROM checks),'[]'::json),
 'website_discovery',(SELECT json_build_object('distinct_retained_ads',count(distinct listing_id),
   'observations',count(*),'deepest_recorded_page',max(source_page))
   FROM listing_snapshots WHERE source_query LIKE 'https://olx.ba/pretraga%'),
 'website_runs',coalesce((SELECT json_agg(runs ORDER BY started_at,id) FROM runs),'[]'::json),
 'observation_provenance',(SELECT json_agg(p) FROM (
   SELECT source_query,source_page,source_per_page,count(*) AS observations,count(distinct listing_id) AS distinct_ads
   FROM listing_snapshots GROUP BY source_query,source_page,source_per_page
   ORDER BY source_query,source_page,source_per_page) p),
 'budget',(SELECT row_to_json(b) FROM website_collection_budget b WHERE id=1),
 'source_pauses',coalesce((SELECT json_agg(p) FROM dealer_source_pauses p),'[]'::json),
 'source_coverage','unknown',
 'sample_label','retained_ads_from_bounded_discovery_not_current_inventory',
 'first_seen_meaning','first_seen_in_retained_database_not_original_publication',
 'sales_or_demand_confidence','not_measured'
) AS coverage;
