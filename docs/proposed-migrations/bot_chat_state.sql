-- OPTIONAL, NOT RECOMMENDED YET, NOT APPLIED. Durable chat memory for the Telegram bot.
-- The bot already remembers the last few messages in memory while its server is warm; this would
-- keep them across restarts. It stores chat text (up to 6 turns of 300 characters per person) in
-- YOUR Supabase project, including your friend's. No code uses this table yet.
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

-- Rollback:
--   DROP TABLE IF EXISTS public.bot_chat_state;
