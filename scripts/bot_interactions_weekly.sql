-- Read-only weekly summary of the Telegram bot interaction log (docs/proposed-migrations/bot_interactions.sql).
-- Run in the Supabase SQL editor as the owner. Nothing here writes. Covers the last 7 days.

-- 1. Overview, week by week (last 8 weeks)
SELECT * FROM public.bot_interactions_weekly ORDER BY week DESC LIMIT 8;

-- 2. Top intents and how they were read
SELECT intent, path, count(*) AS n,
       round(avg(latency_ms)) AS avg_latency_ms,
       count(*) FILTER (WHERE result_count = 0) AS empty_results
FROM public.bot_interactions
WHERE created_at >= (now() AT TIME ZONE 'UTC') - interval '7 days'
GROUP BY intent, path ORDER BY n DESC LIMIT 20;

-- 3. Searches that found nothing (what people asked, with the filters the bot used)
SELECT message_text, filters, count(*) AS n, max(created_at) AS last_seen
FROM public.bot_interactions
WHERE created_at >= (now() AT TIME ZONE 'UTC') - interval '7 days'
  AND result_count = 0 AND message_text IS NOT NULL
GROUP BY message_text, filters ORDER BY n DESC, last_seen DESC LIMIT 25;

-- 4. Gemini phrasing: fallback rate and why
SELECT coalesce(phrasing_reason, '(used)') AS outcome, count(*) AS n,
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
FROM public.bot_interactions
WHERE created_at >= (now() AT TIME ZONE 'UTC') - interval '7 days' AND phrasing IN ('used', 'fallback')
GROUP BY 1 ORDER BY n DESC;

-- 5. Most common unanswered phrases (the bot could not map them, or had to ask back)
SELECT lower(regexp_replace(message_text, '\s+', ' ', 'g')) AS phrase, intent, count(*) AS n
FROM public.bot_interactions
WHERE created_at >= (now() AT TIME ZONE 'UTC') - interval '7 days'
  AND intent IN ('unsupported', 'clarify') AND message_text IS NOT NULL
GROUP BY 1, 2 ORDER BY n DESC LIMIT 25;

-- 6. Errors
SELECT error, count(*) AS n, max(created_at) AS last_seen
FROM public.bot_interactions
WHERE created_at >= (now() AT TIME ZONE 'UTC') - interval '7 days' AND error IS NOT NULL
GROUP BY error ORDER BY n DESC;
