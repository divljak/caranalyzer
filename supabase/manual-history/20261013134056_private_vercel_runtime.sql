-- Server-only role. Login password is provisioned separately as a SCRAM verifier.
-- This role cannot change collector evidence or database schema.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='car_analyzer_app') THEN
    CREATE ROLE car_analyzer_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
  END IF;
END $$;
GRANT CONNECT ON DATABASE postgres TO car_analyzer_app;
GRANT USAGE ON SCHEMA public TO car_analyzer_app;
DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'car_listings','listing_snapshots','scraping_logs','website_collection_runs',
    'website_listing_checks','website_listing_states','website_collection_budget',
    'dealer_listing_details','dealer_vehicle_labels','dealer_source_pauses','dealer_model_searches',
    'dealer_candidates'
  ] LOOP
    EXECUTE format('GRANT SELECT ON TABLE public.%I TO car_analyzer_app', table_name);
    EXECUTE format('CREATE POLICY private_app_read ON public.%I FOR SELECT TO car_analyzer_app USING (true)', table_name);
  END LOOP;
END $$;
GRANT INSERT, DELETE ON public.dealer_model_searches TO car_analyzer_app;
-- The idempotent save only updates the identical fingerprint, never a baseline.
GRANT UPDATE (fingerprint) ON public.dealer_model_searches TO car_analyzer_app;
CREATE POLICY private_app_follow ON public.dealer_model_searches
  FOR INSERT TO car_analyzer_app WITH CHECK (true);
CREATE POLICY private_app_unfollow ON public.dealer_model_searches
  FOR DELETE TO car_analyzer_app USING (true);
CREATE POLICY private_app_idempotent_follow ON public.dealer_model_searches
  FOR UPDATE TO car_analyzer_app USING (true) WITH CHECK (true);
ALTER ROLE car_analyzer_app SET statement_timeout = '20s';
ALTER ROLE car_analyzer_app SET idle_in_transaction_session_timeout = '20s';
