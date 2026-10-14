-- Cover the check-to-run foreign key for run-history lookups.
CREATE INDEX ix_website_listing_checks_run_id
    ON public.website_listing_checks (run_id);
