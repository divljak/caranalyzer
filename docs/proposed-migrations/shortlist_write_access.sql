-- PROPOSED, NOT APPLIED. Nothing in the repository or the deployed bot runs this file.
-- Review it, then paste it into the Supabase SQL editor (as the project owner) if you want the
-- Telegram bot to save a car to your shortlist ("⭐ save this one").
--
-- dealer_candidates is the shortlist the web workspace uses (status: shortlisted / contacted /
-- inspect / passed, plus a note). Today the bot's role (car_analyzer_app) can only read it.
--
-- Least privilege: INSERT and DELETE only, no UPDATE. The bot never changes a status or a note:
-- it adds a car only if it is not already there, and removes only a car it added itself (still
-- "shortlisted" with an empty note). Anything you set on the web is left alone.
GRANT INSERT, DELETE ON public.dealer_candidates TO car_analyzer_app;
DROP POLICY IF EXISTS private_app_candidate_insert ON public.dealer_candidates;
CREATE POLICY private_app_candidate_insert ON public.dealer_candidates
  FOR INSERT TO car_analyzer_app WITH CHECK (true);
DROP POLICY IF EXISTS private_app_candidate_delete ON public.dealer_candidates;
CREATE POLICY private_app_candidate_delete ON public.dealer_candidates
  FOR DELETE TO car_analyzer_app USING (true);

-- Rollback (run to undo everything above):
--   DROP POLICY IF EXISTS private_app_candidate_delete ON public.dealer_candidates;
--   DROP POLICY IF EXISTS private_app_candidate_insert ON public.dealer_candidates;
--   REVOKE INSERT, DELETE ON public.dealer_candidates FROM car_analyzer_app;
