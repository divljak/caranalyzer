-- Extend the existing read-only role; no USAGE/UPDATE, BYPASSRLS or broad data role.
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO car_analyzer_backup;
-- Full pg_dump + snapshot exporter need two sessions; allow the independent health reader.
ALTER ROLE car_analyzer_backup CONNECTION LIMIT 3;
