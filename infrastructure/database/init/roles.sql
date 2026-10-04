\set ON_ERROR_STOP on
SET password_encryption = 'scram-sha-256';
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS btree_gist;
DO $bootstrap$
DECLARE
    credentials jsonb := pg_read_file('/tmp/fleetiq-db-roles.json')::jsonb;
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['fleetiq_app','fleetiq_worker','fleetiq_migration','fleetiq_test_admin','fleetiq_mlflow']
    LOOP
        IF credentials->>role_name IS NULL THEN
            RAISE EXCEPTION 'Missing required role credential';
        END IF;
        EXECUTE format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L',
                       role_name, credentials->>role_name);
    END LOOP;
END
$bootstrap$;
ALTER ROLE fleetiq_test_admin CREATEDB;
GRANT fleetiq_migration TO fleetiq_test_admin;
ALTER DATABASE fleetiq OWNER TO fleetiq_migration;
REVOKE ALL ON DATABASE fleetiq FROM PUBLIC;
GRANT CONNECT ON DATABASE fleetiq TO fleetiq_app, fleetiq_worker, fleetiq_migration, fleetiq_mlflow;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO fleetiq_migration;
GRANT USAGE ON SCHEMA public TO fleetiq_app, fleetiq_worker;
CREATE SCHEMA mlflow AUTHORIZATION fleetiq_mlflow;
ALTER ROLE fleetiq_mlflow IN DATABASE fleetiq SET search_path = mlflow;
