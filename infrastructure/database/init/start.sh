#!/bin/sh
set -eu
umask 077
# Host files stay 0600; only a container-local copy becomes readable by PostgreSQL.
cp /run/secrets/db_roles /tmp/fleetiq-db-roles.json
chown postgres:postgres /tmp/fleetiq-db-roles.json
chmod 0400 /tmp/fleetiq-db-roles.json
exec docker-entrypoint.sh "$@"
