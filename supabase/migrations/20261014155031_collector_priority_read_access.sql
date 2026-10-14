-- The installed coverage priority queue reads enrichment, candidate status and
-- Following filters. This is collector-only read access, never hosted write access.
GRANT SELECT ON public.dealer_listing_details, public.dealer_candidates,
 public.dealer_model_searches TO car_analyzer_collector;

DROP POLICY IF EXISTS collector_priority_read ON public.dealer_listing_details;
CREATE POLICY collector_priority_read ON public.dealer_listing_details
 FOR SELECT TO car_analyzer_collector USING (true);
DROP POLICY IF EXISTS collector_priority_read ON public.dealer_candidates;
CREATE POLICY collector_priority_read ON public.dealer_candidates
 FOR SELECT TO car_analyzer_collector USING (true);
DROP POLICY IF EXISTS collector_priority_read ON public.dealer_model_searches;
CREATE POLICY collector_priority_read ON public.dealer_model_searches
 FOR SELECT TO car_analyzer_collector USING (true);
-- No mutations, schema privilege, role membership, RLS bypass or app grants.
