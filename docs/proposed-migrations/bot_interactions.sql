-- PROPOSED, NOT APPLIED. Interaction log for improving the Telegram bot's answers.
-- Paste into the Supabase SQL editor as the project owner when you want it. Until then the bot
-- silently skips logging (it notices the missing table and stops trying for an hour).
--
-- What a row holds: time, chat type, a salted hash of the Telegram user id (never a name or
-- username), the message with e-mail addresses, phone numbers and @handles redacted (max 400
-- characters), the parsed intent, which path read it (free parser / Gemini / button / command),
-- the filters, result and shown counts, whether Gemini phrased the reply or why it fell back,
-- the button tapped, latency and an error class name. No chat ids, no reply text.
--
-- Least privilege: the bot role (car_analyzer_app) may only INSERT. It cannot read, change or
-- delete rows; reading is for the project owner (SQL editor) or the weekly summary below.
CREATE TABLE IF NOT EXISTS public.bot_interactions (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  created_at       timestamp without time zone NOT NULL DEFAULT (now() AT TIME ZONE 'UTC'),
  chat_type        text CHECK (chat_type IN ('private', 'group')),
  user_hash        text CHECK (user_hash ~ '^[0-9a-f]{24}$'),
  message_text     text CHECK (char_length(message_text) <= 400),
  button           text CHECK (char_length(button) <= 64),
  intent           text CHECK (char_length(intent) <= 40),
  path             text CHECK (char_length(path) <= 40),
  filters          jsonb,
  result_count     integer,
  shown_count      integer,
  phrasing         text CHECK (phrasing IN ('used', 'fallback', 'off')),
  phrasing_reason  text CHECK (char_length(phrasing_reason) <= 80),
  latency_ms       integer,
  error            text CHECK (char_length(error) <= 80)
);
CREATE INDEX IF NOT EXISTS bot_interactions_created_at ON public.bot_interactions (created_at);
ALTER TABLE public.bot_interactions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.bot_interactions FROM PUBLIC;
GRANT INSERT ON public.bot_interactions TO car_analyzer_app;
DROP POLICY IF EXISTS bot_interactions_insert ON public.bot_interactions;
CREATE POLICY bot_interactions_insert ON public.bot_interactions
  FOR INSERT TO car_analyzer_app WITH CHECK (true);

-- Weekly summary (read-only; the owner reads it, the bot cannot). security_invoker keeps RLS in force.
CREATE OR REPLACE VIEW public.bot_interactions_weekly WITH (security_invoker = true) AS
SELECT date_trunc('week', created_at)::date AS week,
       count(*)                                                     AS interactions,
       count(DISTINCT user_hash)                                    AS people,
       count(*) FILTER (WHERE path LIKE '%gemini%')                 AS read_by_gemini,
       count(*) FILTER (WHERE phrasing = 'used')                    AS phrased,
       count(*) FILTER (WHERE phrasing = 'fallback')                AS phrasing_fallbacks,
       round(100.0 * count(*) FILTER (WHERE phrasing = 'fallback')
             / nullif(count(*) FILTER (WHERE phrasing IN ('used', 'fallback')), 0), 1) AS fallback_rate_pct,
       count(*) FILTER (WHERE result_count = 0)                     AS empty_results,
       count(*) FILTER (WHERE intent IN ('unsupported', 'clarify')) AS unanswered,
       percentile_cont(.5) WITHIN GROUP (ORDER BY latency_ms)       AS median_latency_ms,
       count(*) FILTER (WHERE error IS NOT NULL)                    AS errors
FROM public.bot_interactions
GROUP BY 1;
REVOKE ALL ON public.bot_interactions_weekly FROM PUBLIC;

-- Retention: 90 days. With pg_cron enabled (Supabase: Database → Extensions → pg_cron), schedule it:
--   SELECT cron.schedule('bot-interactions-retention', '17 3 * * *',
--     $$DELETE FROM public.bot_interactions WHERE created_at < (now() AT TIME ZONE 'UTC') - interval '90 days'$$);
-- Without pg_cron, run that DELETE by hand (or from the weekly summary routine) at least monthly.

-- Rollback:
--   SELECT cron.unschedule('bot-interactions-retention');  -- only if scheduled
--   DROP VIEW IF EXISTS public.bot_interactions_weekly;
--   DROP TABLE IF EXISTS public.bot_interactions;
