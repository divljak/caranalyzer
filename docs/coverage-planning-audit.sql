-- Read-only planning supplement. Bind :as_of to a UTC timestamp.
-- Each aggregate describes retained ads, not a market census or current inventory.
WITH clock AS (SELECT CAST(:as_of AS timestamp) AS ts), tracked AS (
 SELECT c.*,w.status AS direct_status,w.last_checked_at,w.last_success_at,w.next_check_at,w.details_checked_at,
 CASE WHEN w.details_checked_at IS NOT NULL THEN w.attributes::jsonb->>'Proizvođač'
      ELSE coalesce(v.make,c.make) END AS market_make,
 CASE WHEN w.details_checked_at IS NOT NULL THEN w.attributes::jsonb->>'Model'
      ELSE coalesce(v.model,c.model) END AS market_model,
 CASE WHEN w.details_checked_at IS NOT NULL AND (e.checked_at IS NULL OR w.details_checked_at>=e.checked_at)
      THEN w.transmission ELSE coalesce(e.transmission,c.transmission) END AS effective_transmission,
 (w.details_checked_at IS NOT NULL OR (v.make IS NOT NULL AND v.model IS NOT NULL)) AS recognized_model,
 (w.transmission IS NOT NULL OR e.transmission IS NOT NULL) AS structured_transmission,
 v.category
 FROM car_listings c LEFT JOIN website_listing_states w USING(listing_id)
 LEFT JOIN dealer_vehicle_labels v USING(listing_id) LEFT JOIN dealer_listing_details e USING(listing_id)
 WHERE c.source='olx.ba' AND c.collection_method IN ('olx_api','olx_website')
), models AS (
 SELECT market_make,market_model,count(*) AS retained,
 count(*) FILTER(WHERE price_status='priced' AND price>0) AS priced,
 count(*) FILTER(WHERE price_status='priced' AND price>0 AND last_verified_at>=(SELECT ts FROM clock)-interval '7 days') AS priced_observed_7d,
 count(*) FILTER(WHERE price_status='priced' AND price>0 AND last_verified_at>=(SELECT ts FROM clock)-interval '30 days') AS priced_observed_30d,
 count(*) FILTER(WHERE last_success_at>=(SELECT ts FROM clock)-interval '7 days') AS successful_direct_7d,
 count(*) FILTER(WHERE last_verified_at<(SELECT ts FROM clock)-interval '30 days') AS observed_older_30d,
 count(*) FILTER(WHERE effective_transmission IS NULL) AS gearbox_unknown
 FROM tracked WHERE recognized_model GROUP BY market_make,market_model
), cohorts AS (
 SELECT market_make,market_model,count(*) AS retained,
 count(*) FILTER(WHERE price_status='priced' AND price>0) AS priced,
 count(*) FILTER(WHERE price_status='priced' AND price>0 AND last_verified_at>=(SELECT ts FROM clock)-interval '30 days') AS priced_observed_30d_now,
 count(*) FILTER(WHERE last_success_at>=(SELECT ts FROM clock)-interval '7 days') AS successful_direct_7d
 FROM tracked WHERE recognized_model AND year BETWEEN 2020 AND 2023
 AND mileage BETWEEN 50000 AND 150000 AND fuel_type='diesel' AND effective_transmission='automatic'
 AND category='suv' GROUP BY market_make,market_model
), daily AS (
 SELECT date(started_at) AS utc_day,sum(search_attempted) AS discovery_attempts,sum(detail_attempted) AS direct_attempts,
 sum(details_completed) AS direct_successes,sum(new_listings) AS first_seen_database_ids,
 sum(observations) AS observations,count(*) AS runs,
 count(*) FILTER(WHERE status='completed') AS completed_runs
 FROM website_collection_runs WHERE started_at>=(SELECT ts FROM clock)-interval '14 days'
 GROUP BY 1 ORDER BY 1
), history AS (
 SELECT s.*,lag(s.asking_price) OVER(PARTITION BY s.listing_id ORDER BY s.observed_at,s.id) AS prior_price,
 lag(s.price_status) OVER(PARTITION BY s.listing_id ORDER BY s.observed_at,s.id) AS prior_status,
 row_number() OVER(PARTITION BY s.listing_id ORDER BY s.observed_at,s.id) AS position
 FROM listing_snapshots s JOIN tracked t USING(listing_id)
)
SELECT json_build_object(
 'as_of_utc',(SELECT ts FROM clock),
 'observation_ages',(SELECT json_build_object(
   '0_to_1_days',count(*) FILTER(WHERE last_verified_at >=(SELECT ts FROM clock)-interval '1 day'),
   '1_to_7_days',count(*) FILTER(WHERE last_verified_at <(SELECT ts FROM clock)-interval '1 day' AND last_verified_at >=(SELECT ts FROM clock)-interval '7 days'),
   '7_to_30_days',count(*) FILTER(WHERE last_verified_at <(SELECT ts FROM clock)-interval '7 days' AND last_verified_at >=(SELECT ts FROM clock)-interval '30 days'),
   '30_to_90_days',count(*) FILTER(WHERE last_verified_at <(SELECT ts FROM clock)-interval '30 days' AND last_verified_at >=(SELECT ts FROM clock)-interval '90 days'),
   'older_90_days',count(*) FILTER(WHERE last_verified_at <(SELECT ts FROM clock)-interval '90 days'),
   'unknown',count(*) FILTER(WHERE last_verified_at IS NULL)) FROM tracked),
 'field_and_status_quality',(SELECT json_build_object(
   'malformed_source_ids',count(*) FILTER(WHERE listing_id !~ '^[0-9]+$'),
   'year_outside_default',count(*) FILTER(WHERE year<1990 OR year>2026),
   'mileage_outside_configured_bounds',count(*) FILTER(WHERE mileage<0 OR mileage>500000),
   'year_missing',count(*) FILTER(WHERE year IS NULL),
   'mileage_missing',count(*) FILTER(WHERE mileage IS NULL),
   'fuel_missing',count(*) FILTER(WHERE fuel_type IS NULL OR fuel_type=''),
   'price_missing_or_unusable',count(*) FILTER(WHERE price_status IS DISTINCT FROM 'priced' OR price IS NULL OR price<=0),
   'model_unrecognized',count(*) FILTER(WHERE NOT recognized_model),
   'gearbox_unknown',count(*) FILTER(WHERE effective_transmission IS NULL),
   'structured_gearbox_known',count(*) FILTER(WHERE structured_transmission),
   'body_category_unknown',count(*) FILTER(WHERE category IS NULL),
   'inactive_legacy_flag',count(*) FILTER(WHERE NOT is_active),
   'direct_terminal_status',count(*) FILTER(WHERE direct_status IN ('removed_unconfirmed','source_reported_ended','source_reported_sold')),
   'direct_listed_with_no_success',count(*) FILTER(WHERE direct_status='listed' AND last_success_at IS NULL),
   'unknown_direct_status',count(*) FILTER(WHERE direct_status IS NULL OR direct_status='unknown')) FROM tracked),
 'model_sample_summary',(SELECT json_build_object('recognized_groups',count(*),
   'groups_with_10_priced_all_ages',count(*) FILTER(WHERE priced>=10),
   'groups_with_10_priced_observed_30d',count(*) FILTER(WHERE priced_observed_30d>=10),
   'groups_with_10_priced_observed_7d',count(*) FILTER(WHERE priced_observed_7d>=10)) FROM models),
 'largest_models',(SELECT json_agg(m) FROM (SELECT * FROM models ORDER BY retained DESC,market_make,market_model LIMIT 20)m),
 'diagnostic_ui_spec_cohorts',(SELECT json_agg(c) FROM (SELECT * FROM cohorts ORDER BY retained DESC,market_make,market_model LIMIT 20)c),
 'daily_website_runs',(SELECT json_agg(d) FROM daily d),
 'history',(SELECT json_build_object('tracked_snapshots',count(*),'distinct_ads',count(distinct listing_id),
   'repeat_observations',count(*) FILTER(WHERE position>1),
   'unchanged_repeat_prices',count(*) FILTER(WHERE position>1 AND asking_price IS NOT DISTINCT FROM prior_price AND price_status IS NOT DISTINCT FROM prior_status),
   'first_observation',min(observed_at),'last_observation',max(observed_at)) FROM history),
 'duplicate_observation_groups',(SELECT count(*) FROM (SELECT listing_id,observed_at,source_query FROM listing_snapshots GROUP BY 1,2,3 HAVING count(*)>1)d),
 'orphan_snapshots',(SELECT count(*) FROM listing_snapshots s LEFT JOIN car_listings c USING(listing_id) WHERE c.listing_id IS NULL)
) AS planning;
