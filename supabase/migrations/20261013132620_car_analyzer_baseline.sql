-- Application schema copied from the verified local evidence database.
-- No application sign-in. Database Data API access stays closed.
-- Runtime roles and hosted app configuration are separate from this import.
--
-- PostgreSQL database dump
--


-- Dumped from database version 16.15
-- Dumped by pg_dump version 16.15


--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--



--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--





--
-- Name: car_listings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.car_listings (
    listing_id character varying(50) NOT NULL,
    make character varying(50) NOT NULL,
    model character varying(100) NOT NULL,
    year integer,
    price integer,
    mileage integer,
    views integer,
    posted_date date,
    location character varying(100),
    seller_type character varying(20),
    fuel_type character varying(20),
    transmission character varying(20),
    listing_url character varying(500),
    description text,
    scraped_at timestamp without time zone,
    source character varying(30) NOT NULL,
    first_seen_at timestamp without time zone,
    last_verified_at timestamp without time zone,
    is_active boolean,
    collection_method character varying(30) DEFAULT 'legacy_rendered'::character varying NOT NULL,
    price_status character varying(30) DEFAULT 'priced'::character varying NOT NULL,
    first_price integer,
    previous_price integer,
    price_change_count integer DEFAULT 0 NOT NULL,
    last_price_change_at timestamp without time zone,
    last_source_query character varying(500),
    missing_from_window_since_at timestamp without time zone,
    consecutive_missing_runs integer DEFAULT 0 NOT NULL
);


--
-- Name: dealer_candidates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dealer_candidates (
    listing_id character varying(50) NOT NULL,
    status character varying(20) DEFAULT 'shortlisted'::character varying NOT NULL,
    note text DEFAULT ''::text NOT NULL,
    saved_at timestamp without time zone DEFAULT (now() AT TIME ZONE 'UTC'::text) NOT NULL,
    updated_at timestamp without time zone DEFAULT (now() AT TIME ZONE 'UTC'::text) NOT NULL,
    CONSTRAINT dealer_candidates_status_check CHECK (((status)::text = ANY (ARRAY[('shortlisted'::character varying)::text, ('contacted'::character varying)::text, ('inspect'::character varying)::text, ('passed'::character varying)::text])))
);


--
-- Name: dealer_listing_details; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dealer_listing_details (
    listing_id character varying(50) NOT NULL,
    transmission character varying(20),
    raw_value character varying(80),
    source_url character varying(200) NOT NULL,
    checked_at timestamp without time zone NOT NULL,
    outcome character varying(30) NOT NULL,
    CONSTRAINT dealer_listing_details_outcome_check CHECK (((outcome)::text = ANY (ARRAY[('collected'::character varying)::text, ('not_specified'::character varying)::text, ('unavailable'::character varying)::text]))),
    CONSTRAINT dealer_listing_details_transmission_check CHECK (((transmission)::text = ANY (ARRAY[('manual'::character varying)::text, ('automatic'::character varying)::text])))
);


--
-- Name: dealer_model_searches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dealer_model_searches (
    id character varying(32) NOT NULL,
    fingerprint character varying(64) NOT NULL,
    filters jsonb NOT NULL,
    baseline jsonb NOT NULL,
    baseline_as_of timestamp without time zone NOT NULL,
    saved_at timestamp without time zone DEFAULT (now() AT TIME ZONE 'UTC'::text) NOT NULL
);


--
-- Name: dealer_source_pauses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dealer_source_pauses (
    source character varying(40) NOT NULL,
    resume_at timestamp without time zone NOT NULL
);


--
-- Name: dealer_vehicle_labels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dealer_vehicle_labels (
    listing_id character varying(50) NOT NULL,
    make character varying(80),
    model character varying(100),
    category character varying(20),
    category_basis character varying(30),
    source_title text,
    source_make character varying(80),
    source_model character varying(100),
    version integer DEFAULT 0 NOT NULL
);


--
-- Name: listing_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.listing_snapshots (
    id integer NOT NULL,
    listing_id character varying(50) NOT NULL,
    observed_at timestamp without time zone NOT NULL,
    asking_price integer NOT NULL,
    views integer,
    source_url character varying(500) NOT NULL,
    title character varying(500) NOT NULL,
    is_active boolean NOT NULL,
    source_query character varying(500),
    source_page integer,
    source_per_page integer,
    price_status character varying(30) DEFAULT 'priced'::character varying NOT NULL
);


--
-- Name: listing_snapshots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.listing_snapshots_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: listing_snapshots_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.listing_snapshots_id_seq OWNED BY public.listing_snapshots.id;


--
-- Name: scraping_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.scraping_logs (
    id integer NOT NULL,
    session_id character varying(50) NOT NULL,
    start_time timestamp without time zone,
    end_time timestamp without time zone,
    total_listings_found integer,
    new_listings_added integer,
    updated_listings integer,
    errors_count integer,
    status character varying(20),
    error_message text,
    source_query character varying(500),
    pages_requested integer,
    source_per_page integer,
    missing_from_window_count integer DEFAULT 0 NOT NULL
);


--
-- Name: scraping_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.scraping_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: scraping_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.scraping_logs_id_seq OWNED BY public.scraping_logs.id;


--
-- Name: website_collection_budget; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.website_collection_budget (
    id integer NOT NULL,
    day date NOT NULL,
    search_visits integer NOT NULL,
    detail_visits integer NOT NULL,
    last_visit_at timestamp without time zone,
    pause_until timestamp without time zone
);


--
-- Name: website_collection_budget_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.website_collection_budget_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: website_collection_budget_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.website_collection_budget_id_seq OWNED BY public.website_collection_budget.id;


--
-- Name: website_collection_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.website_collection_runs (
    id integer NOT NULL,
    session_id character varying(50) NOT NULL,
    started_at timestamp without time zone NOT NULL,
    finished_at timestamp without time zone,
    status character varying(30) NOT NULL,
    source_query character varying(500) NOT NULL,
    search_limit integer NOT NULL,
    detail_limit integer NOT NULL,
    search_attempted integer NOT NULL,
    detail_attempted integer NOT NULL,
    discovery_complete integer NOT NULL,
    details_completed integer NOT NULL,
    observations integer NOT NULL,
    new_listings integer NOT NULL,
    errors integer NOT NULL,
    report text
);


--
-- Name: website_collection_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.website_collection_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: website_collection_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.website_collection_runs_id_seq OWNED BY public.website_collection_runs.id;


--
-- Name: website_listing_checks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.website_listing_checks (
    id integer NOT NULL,
    listing_id character varying(50) NOT NULL,
    run_id integer NOT NULL,
    checked_at timestamp without time zone NOT NULL,
    outcome character varying(40) NOT NULL,
    source_url character varying(500) NOT NULL,
    evidence text NOT NULL
);


--
-- Name: website_listing_checks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.website_listing_checks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: website_listing_checks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.website_listing_checks_id_seq OWNED BY public.website_listing_checks.id;


--
-- Name: website_listing_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.website_listing_states (
    listing_id character varying(50) NOT NULL,
    status character varying(40) NOT NULL,
    last_checked_at timestamp without time zone,
    last_success_at timestamp without time zone,
    last_listed_at timestamp without time zone,
    last_outcome character varying(40),
    unavailable_since timestamp without time zone,
    unavailable_checks integer NOT NULL,
    next_check_at timestamp without time zone,
    details_checked_at timestamp without time zone,
    transmission character varying(20),
    attributes text,
    published_date date,
    renewed_at timestamp without time zone
);


--
-- Name: listing_snapshots id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.listing_snapshots ALTER COLUMN id SET DEFAULT nextval('public.listing_snapshots_id_seq'::regclass);


--
-- Name: scraping_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scraping_logs ALTER COLUMN id SET DEFAULT nextval('public.scraping_logs_id_seq'::regclass);


--
-- Name: website_collection_budget id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.website_collection_budget ALTER COLUMN id SET DEFAULT nextval('public.website_collection_budget_id_seq'::regclass);


--
-- Name: website_collection_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.website_collection_runs ALTER COLUMN id SET DEFAULT nextval('public.website_collection_runs_id_seq'::regclass);


--
-- Name: website_listing_checks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.website_listing_checks ALTER COLUMN id SET DEFAULT nextval('public.website_listing_checks_id_seq'::regclass);


--
-- Name: car_listings car_listings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.car_listings
    ADD CONSTRAINT car_listings_pkey PRIMARY KEY (listing_id);


--
-- Name: dealer_candidates dealer_candidates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dealer_candidates
    ADD CONSTRAINT dealer_candidates_pkey PRIMARY KEY (listing_id);


--
-- Name: dealer_listing_details dealer_listing_details_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dealer_listing_details
    ADD CONSTRAINT dealer_listing_details_pkey PRIMARY KEY (listing_id);


--
-- Name: dealer_model_searches dealer_model_searches_fingerprint_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dealer_model_searches
    ADD CONSTRAINT dealer_model_searches_fingerprint_key UNIQUE (fingerprint);


--
-- Name: dealer_model_searches dealer_model_searches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dealer_model_searches
    ADD CONSTRAINT dealer_model_searches_pkey PRIMARY KEY (id);


--
-- Name: dealer_source_pauses dealer_source_pauses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dealer_source_pauses
    ADD CONSTRAINT dealer_source_pauses_pkey PRIMARY KEY (source);


--
-- Name: dealer_vehicle_labels dealer_vehicle_labels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dealer_vehicle_labels
    ADD CONSTRAINT dealer_vehicle_labels_pkey PRIMARY KEY (listing_id);


--
-- Name: listing_snapshots listing_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.listing_snapshots
    ADD CONSTRAINT listing_snapshots_pkey PRIMARY KEY (id);


--
-- Name: scraping_logs scraping_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scraping_logs
    ADD CONSTRAINT scraping_logs_pkey PRIMARY KEY (id);


--
-- Name: website_collection_budget website_collection_budget_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.website_collection_budget
    ADD CONSTRAINT website_collection_budget_pkey PRIMARY KEY (id);


--
-- Name: website_collection_runs website_collection_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.website_collection_runs
    ADD CONSTRAINT website_collection_runs_pkey PRIMARY KEY (id);


--
-- Name: website_collection_runs website_collection_runs_session_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.website_collection_runs
    ADD CONSTRAINT website_collection_runs_session_id_key UNIQUE (session_id);


--
-- Name: website_listing_checks website_listing_checks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.website_listing_checks
    ADD CONSTRAINT website_listing_checks_pkey PRIMARY KEY (id);


--
-- Name: website_listing_states website_listing_states_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.website_listing_states
    ADD CONSTRAINT website_listing_states_pkey PRIMARY KEY (listing_id);


--
-- Name: ix_listing_snapshots_listing_observed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_listing_snapshots_listing_observed ON public.listing_snapshots USING btree (listing_id, observed_at);


--
-- Name: ix_website_listing_checks_listing_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_website_listing_checks_listing_id ON public.website_listing_checks USING btree (listing_id);


--
-- Name: dealer_candidates dealer_candidates_listing_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dealer_candidates
    ADD CONSTRAINT dealer_candidates_listing_id_fkey FOREIGN KEY (listing_id) REFERENCES public.car_listings(listing_id) ON DELETE RESTRICT;


--
-- Name: dealer_listing_details dealer_listing_details_listing_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dealer_listing_details
    ADD CONSTRAINT dealer_listing_details_listing_id_fkey FOREIGN KEY (listing_id) REFERENCES public.car_listings(listing_id) ON DELETE RESTRICT;


--
-- Name: dealer_vehicle_labels dealer_vehicle_labels_listing_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dealer_vehicle_labels
    ADD CONSTRAINT dealer_vehicle_labels_listing_id_fkey FOREIGN KEY (listing_id) REFERENCES public.car_listings(listing_id) ON DELETE RESTRICT;


--
-- Name: listing_snapshots listing_snapshots_listing_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.listing_snapshots
    ADD CONSTRAINT listing_snapshots_listing_id_fkey FOREIGN KEY (listing_id) REFERENCES public.car_listings(listing_id) ON DELETE CASCADE;


--
-- Name: website_listing_checks website_listing_checks_listing_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.website_listing_checks
    ADD CONSTRAINT website_listing_checks_listing_id_fkey FOREIGN KEY (listing_id) REFERENCES public.car_listings(listing_id) ON DELETE RESTRICT;


--
-- Name: website_listing_checks website_listing_checks_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.website_listing_checks
    ADD CONSTRAINT website_listing_checks_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.website_collection_runs(id) ON DELETE RESTRICT;


--
-- Name: website_listing_states website_listing_states_listing_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.website_listing_states
    ADD CONSTRAINT website_listing_states_listing_id_fkey FOREIGN KEY (listing_id) REFERENCES public.car_listings(listing_id) ON DELETE RESTRICT;


--
-- PostgreSQL database dump complete
--


ALTER TABLE public.car_listings ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.car_listings FROM PUBLIC, anon, authenticated;
ALTER TABLE public.dealer_candidates ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.dealer_candidates FROM PUBLIC, anon, authenticated;
ALTER TABLE public.dealer_listing_details ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.dealer_listing_details FROM PUBLIC, anon, authenticated;
ALTER TABLE public.dealer_model_searches ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.dealer_model_searches FROM PUBLIC, anon, authenticated;
ALTER TABLE public.dealer_source_pauses ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.dealer_source_pauses FROM PUBLIC, anon, authenticated;
ALTER TABLE public.dealer_vehicle_labels ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.dealer_vehicle_labels FROM PUBLIC, anon, authenticated;
ALTER TABLE public.listing_snapshots ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.listing_snapshots FROM PUBLIC, anon, authenticated;
ALTER TABLE public.scraping_logs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.scraping_logs FROM PUBLIC, anon, authenticated;
ALTER TABLE public.website_collection_budget ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.website_collection_budget FROM PUBLIC, anon, authenticated;
ALTER TABLE public.website_collection_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.website_collection_runs FROM PUBLIC, anon, authenticated;
ALTER TABLE public.website_listing_checks ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.website_listing_checks FROM PUBLIC, anon, authenticated;
ALTER TABLE public.website_listing_states ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.website_listing_states FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC, anon, authenticated;
