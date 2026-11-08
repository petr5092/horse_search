-- Runs once on an empty horse-s1 PostgreSQL volume, as platform_owner.
-- Example passwords are only for this loopback development stand.
CREATE ROLE platform_app LOGIN PASSWORD 'local-platform-password';
CREATE ROLE annotation_migrator LOGIN PASSWORD 'local-annotation-migrator-password';
CREATE ROLE annotation_app LOGIN PASSWORD 'local-annotation-password';

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO platform_app;
GRANT CONNECT ON DATABASE horse_s1 TO platform_app, annotation_migrator, annotation_app;
ALTER ROLE platform_app IN DATABASE horse_s1 SET search_path = public;

-- Alembic runs as platform_owner. These grants also apply to future tables.
ALTER DEFAULT PRIVILEGES FOR ROLE platform_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO platform_app;
ALTER DEFAULT PRIVILEGES FOR ROLE platform_owner IN SCHEMA public
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO platform_app;

CREATE SCHEMA annotation AUTHORIZATION annotation_migrator;
REVOKE ALL ON SCHEMA annotation FROM PUBLIC;
GRANT USAGE ON SCHEMA annotation TO annotation_app;
ALTER ROLE annotation_migrator IN DATABASE horse_s1 SET search_path = annotation;
ALTER ROLE annotation_app IN DATABASE horse_s1 SET search_path = annotation;
-- Go migrations grant exact table/sequence privileges to annotation_app.
-- No default DML grants: history and snapshots must remain immutable.
