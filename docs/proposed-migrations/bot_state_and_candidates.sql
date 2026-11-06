-- PROPOSED, NOT APPLIED. Nothing in the repository or the deployed bot runs this file.
-- Review it, then paste it into the Supabase SQL editor (as the project owner) only if you want:
--   (1) durable chat memory for the Telegram bot, and
--   (2) the bot to save a specific car to the shortlist (the same table the web workspace uses).
-- Everything here is additive and reversible (see the rollback section at the bottom).

-- ---------------------------------------------------------------------------------------------
-- 1) Durable chat memory
--    One row per (chat, user): the language they write in, the scope of their last search, and
--    the last few chat turns (the bot keeps at most 6 turns of up to 300 characters each).
--    This stores chat text in YOUR Supabase project. The bot deletes rows older than 30 days.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.bot_chat_state (
  chat_id    bigint NOT NULL,
  user_id    bigint NOT NULL,
  language   text,
  scope      jsonb,
  history    jsonb NOT NULL DEFAULT '[]'::jsonb,
  updated_at timestamp without time zone NOT NULL DEFAULT (now() AT TIME ZONE 'UTC'),
  PRIMARY KEY (chat_id, user_id)
);
ALTER TABLE public.bot_chat_state ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.bot_chat_state TO car_analyzer_app;
DROP POLICY IF EXISTS bot_state_rw ON public.bot_chat_state;
CREATE POLICY bot_state_rw ON public.bot_chat_state
  FOR ALL TO car_analyzer_app USING (true) WITH CHECK (true);

-- ---------------------------------------------------------------------------------------------
-- 2) Saving a car to the shortlist ("save this one")
--    dealer_candidates is the shortlist the web workspace uses (status: shortlisted / contacted /
--    inspect / passed, plus a note). Today the app role can only read it. This lets the bot's role
--    insert, update and remove shortlist rows, and nothing else on that table.
-- ---------------------------------------------------------------------------------------------
GRANT INSERT, UPDATE, DELETE ON public.dealer_candidates TO car_analyzer_app;
DROP POLICY IF EXISTS private_app_candidate_write ON public.dealer_candidates;
CREATE POLICY private_app_candidate_write ON public.dealer_candidates
  FOR ALL TO car_analyzer_app USING (true) WITH CHECK (true);

-- ---------------------------------------------------------------------------------------------
-- Rollback (run to undo everything above):
--   DROP POLICY IF EXISTS private_app_candidate_write ON public.dealer_candidates;
--   REVOKE INSERT, UPDATE, DELETE ON public.dealer_candidates FROM car_analyzer_app;
--   DROP TABLE IF EXISTS public.bot_chat_state;
-- ---------------------------------------------------------------------------------------------
