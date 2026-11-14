-- PROPOSED, NOT APPLIED. Durable "one morning digest per day" record for the Telegram bot.
-- Paste into the Supabase SQL editor as the project owner when you want it. Until then the bot keeps
-- using its in-process (or TELEGRAM_DIGEST_LEDGER file) ledger, exactly as today.
--
-- Why: the in-process ledger is lost when Vercel starts a new instance, so a retried or repeated cron
-- call could send the digest twice. With this table the first claim for a Sarajevo date wins, across
-- instances and deploys.
--
-- Least privilege for the bot role (car_analyzer_app): it can read the ledger, insert a claim, mark it
-- sent, and delete only a claim that was never sent (to retry after a failed Telegram send).
CREATE TABLE IF NOT EXISTS public.telegram_digest_sends (
  day         date PRIMARY KEY,                -- the Sarajevo calendar day the digest is for
  chat_id     bigint NOT NULL,
  claimed_at  timestamp without time zone NOT NULL DEFAULT (now() AT TIME ZONE 'UTC'),
  sent_at     timestamp without time zone,
  message_id  bigint
);
ALTER TABLE public.telegram_digest_sends ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, DELETE ON public.telegram_digest_sends TO car_analyzer_app;
GRANT UPDATE (sent_at, message_id) ON public.telegram_digest_sends TO car_analyzer_app;
DROP POLICY IF EXISTS digest_sends_read ON public.telegram_digest_sends;
CREATE POLICY digest_sends_read ON public.telegram_digest_sends
  FOR SELECT TO car_analyzer_app USING (true);
DROP POLICY IF EXISTS digest_sends_claim ON public.telegram_digest_sends;
CREATE POLICY digest_sends_claim ON public.telegram_digest_sends
  FOR INSERT TO car_analyzer_app WITH CHECK (sent_at IS NULL);
DROP POLICY IF EXISTS digest_sends_mark ON public.telegram_digest_sends;
CREATE POLICY digest_sends_mark ON public.telegram_digest_sends
  FOR UPDATE TO car_analyzer_app USING (sent_at IS NULL) WITH CHECK (sent_at IS NOT NULL);
DROP POLICY IF EXISTS digest_sends_release ON public.telegram_digest_sends;
CREATE POLICY digest_sends_release ON public.telegram_digest_sends
  FOR DELETE TO car_analyzer_app USING (sent_at IS NULL);

-- Rollback:
--   DROP TABLE IF EXISTS public.telegram_digest_sends;
