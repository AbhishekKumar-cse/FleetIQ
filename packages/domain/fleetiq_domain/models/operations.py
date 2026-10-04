"""Scoped identities, durable leases, ordered events and append-only audit."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import AddConstraint

from fleetiq_domain.db import Base
from fleetiq_domain.models import fleet, history, inventory, predictions, work
from fleetiq_domain.models.schema import entity

User = entity(
    "app_user",
    {
        "subject": "text:required",
        "display_name": "text:required",
        "password_hash": "text:required",
        "active": "bool:required:true",
        "password_version": "int:required:0",
    },
    checks=(
        "subject=lower(subject) AND length(trim(subject))>0",
        "password_hash LIKE '$argon2id$%'",
        "password_version>=0",
    ),
    unique=(("subject",),),
)
Role = entity(
    "role", {"code": "text:required", "permissions": "json:required"}, unique=(("code",),)
)
RoleAssignment = entity(
    "role_assignment",
    {
        "user_id": "uuid:required",
        "role_id": "uuid:required",
        "scope_kind": "text:required",
        "fleet_id": "uuid:optional",
        "site_id": "uuid:optional",
        "active": "bool:required:true",
    },
    refs={"user_id": "app_user", "role_id": "role", "fleet_id": "fleet", "site_id": "site"},
    checks=(
        "(scope_kind='organization' AND fleet_id IS NULL AND site_id IS NULL) OR "
        "(scope_kind='fleet' AND fleet_id IS NOT NULL AND site_id IS NULL) OR "
        "(scope_kind='site' AND site_id IS NOT NULL AND fleet_id IS NULL)",
    ),
)
sa.Index(
    "uq_role_assignment_scope",
    RoleAssignment.organization_id,
    RoleAssignment.user_id,
    RoleAssignment.role_id,
    RoleAssignment.fleet_id,
    RoleAssignment.site_id,
    unique=True,
    postgresql_nulls_not_distinct=True,
)
Session = entity(
    "auth_session",
    {
        "user_id": "uuid:required",
        "refresh_hash": "text:required",
        "csrf_hash": "text:required",
        "issued_at": "time:required",
        "last_active_at": "time:required",
        "expires_at": "time:required",
        "revoked": "bool:required:false",
        "revoked_at": "time:optional",
        "rotation": "int:required:0",
        "password_version": "int:required",
    },
    refs={"user_id": "app_user"},
    checks=(
        "refresh_hash ~ '^[0-9a-f]{64}$' AND csrf_hash ~ '^[0-9a-f]{64}$'",
        "issued_at<=last_active_at AND last_active_at<expires_at AND expires_at<=issued_at+interval '8 hours'",
        "(revoked_at IS NOT NULL)=revoked",
        "rotation>=0 AND password_version>=0",
    ),
    unique=(("refresh_hash",),),
)
RefreshTokenHistory = entity(
    "refresh_token_history",
    {
        "session_id": "uuid:required",
        "refresh_hash": "text:required",
        "consumed_at": "time:required",
    },
    refs={"session_id": "auth_session"},
    checks=("refresh_hash ~ '^[0-9a-f]{64}$'",),
    unique=(("refresh_hash",),),
)
SourceCredential = entity(
    "source_credential",
    {
        "source_id": "uuid:required",
        "credential_ref": "text:required",
        "key_id": "text:required",
        "expires_at": "time:optional",
        "revoked": "bool:required:false",
    },
    refs={"source_id": "source"},
    checks=("credential_ref LIKE 'secret://%'",),
    unique=(("source_id", "key_id"),),
)
Job = entity(
    "job",
    {
        "owner_id": "uuid:required",
        "kind": "text:required",
        "input_hash": "text:required",
        "input": "json:required",
        "idempotency_key": "text:required",
        "state": "text:required:'pending'",
        "attempt": "int:required:0",
        "max_attempts": "int:required:6",
        "available_at": "time:required:now()",
        "lease_owner": "text:optional",
        "lease_expires_at": "time:optional",
        "progress": "float:required:0",
        "error": "text:optional",
        "result": "json:optional",
    },
    refs={"owner_id": "app_user"},
    checks=(
        "input_hash ~ '^[0-9a-f]{64}$'",
        "state IN ('pending','running','completed','failed','cancelled','dead_letter','unsupported')",
        "max_attempts BETWEEN 1 AND 6 AND attempt BETWEEN 0 AND max_attempts",
        "(state='running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL AND attempt>0) OR "
        "(state<>'running' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
        "progress>=0 AND progress<=1",
        "state NOT IN ('failed','dead_letter','unsupported') OR error IS NOT NULL",
        "state<>'completed' OR (result IS NOT NULL AND progress=1)",
    ),
    unique=(("kind", "input_hash", "idempotency_key"),),
)
EventOutbox = entity(
    "event_outbox",
    {
        "id": "int:required",
        "scope_kind": "text:required",
        "scope_id": "uuid:optional",
        "kind": "text:required",
        "payload": "json:required",
        "occurred_at": "time:required:now()",
    },
    primary=("id",),
    identities=("id",),
    unique=(("id",),),
    checks=("jsonb_typeof(payload)='object' AND octet_length(payload::text)<=65536",),
)
AuditEvent = entity(
    "audit_event",
    {
        "id": "int:required",
        "actor_id": "uuid:optional",
        "action": "text:required",
        "target_kind": "text:required",
        "target_id": "uuid:optional",
        "scope_kind": "text:required",
        "scope_id": "uuid:optional",
        "occurred_at": "time:required:now()",
        "versions": "json:required",
        "reason": "text:required",
    },
    primary=("id",),
    identities=("id",),
    refs={"actor_id": "app_user"},
    unique=(("id",),),
    checks=("length(trim(reason))>0",),
)
ImportBatch = entity(
    "import_batch",
    {
        "source_id": "uuid:required",
        "checksum": "text:required",
        "kind": "text:required",
        "state": "text:required:'pending'",
        "total_rows": "int:required:0",
        "accepted_rows": "int:required:0",
        "quarantined_rows": "int:required:0",
        "manifest_hash": "text:optional",
        "summary": "json:optional",
    },
    refs={"source_id": "source"},
    checks=(
        "checksum ~ '^[0-9a-f]{64}$'",
        "state IN ('pending','validating','completed','failed')",
        "total_rows>=0 AND accepted_rows>=0 AND quarantined_rows>=0 AND total_rows=accepted_rows+quarantined_rows",
    ),
    unique=(("source_id", "checksum", "kind"),),
)
Quarantine = entity(
    "quarantine",
    {
        "batch_id": "uuid:required",
        "row_number": "int:required",
        "raw_row": "json:required",
        "reason": "text:required",
    },
    refs={"batch_id": "import_batch"},
    checks=("row_number>=0",),
    unique=(("batch_id", "row_number"),),
)
ReportArtifact = entity(
    "report_artifact",
    {
        "owner_id": "uuid:required",
        "job_id": "uuid:required",
        "scenario_run_id": "uuid:optional",
        "relative_path": "text:required",
        "content_hash": "text:required",
        "scope_kind": "text:required",
        "scope_id": "uuid:optional",
    },
    refs={"owner_id": "app_user", "job_id": "job", "scenario_run_id": "scenario_run"},
    checks=(
        "content_hash ~ '^[0-9a-f]{64}$'",
        "relative_path<>'' AND relative_path NOT LIKE '/%' AND relative_path !~ '(^|/)\\.\\.(/|$)' "
        "AND position(chr(92) in relative_path)=0 AND position(':' in relative_path)=0",
    ),
    unique=(("relative_path",),),
)

# Pre-authentication counters are keyed by digests, with no subject or tenant data in plaintext.
rate_table = sa.Table(
    "auth_rate_limit",
    Base.metadata,
    sa.Column("key_hash", sa.Text, primary_key=True),
    sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column("attempts", sa.BigInteger, nullable=False),
    sa.CheckConstraint("attempts>=0", name="attempts"),
    sa.CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="hash"),
)
AuthRateLimit = type("AuthRateLimit", (), {})
Base.registry.map_imperatively(AuthRateLimit, rate_table)
sa.Index("ix_job_ready", Job.state, Job.available_at, Job.lease_expires_at)
sa.Index("ix_job_pending_lease", Job.organization_id, Job.state, Job.lease_expires_at)
sa.Index("ix_outbox_org_sequence", EventOutbox.organization_id, EventOutbox.id)
sa.Index(
    "ix_audit_scope_time", AuditEvent.organization_id, AuditEvent.scope_kind, AuditEvent.occurred_at
)
sa.Index(
    "ix_audit_target", AuditEvent.organization_id, AuditEvent.target_kind, AuditEvent.target_id
)
EXTRA = []
DOWN_EXTRA = []
for model, field in (
    (history.MaintenanceEvent, "actor_id"),
    (history.FailureEvent, "confirmed_by"),
    (history.Inspection, "inspector_id"),
    (work.Approval, "actor_id"),
    (inventory.StockMovement, "actor_id"),
    (predictions.PolicyVersion, "actor_id"),
    (predictions.ModelDeployment, "actor_id"),
    (predictions.Alert, "acknowledged_by"),
    (fleet.AircraftStatusEvent, "actor_id"),
    (fleet.SchedulePlan, "approved_by"),
    (fleet.Scenario, "owner_id"),
):
    constraint = sa.ForeignKeyConstraint(
        ["organization_id", field],
        ["app_user.organization_id", "app_user.id"],
        name=f"fk_{model.__table__.name}_{field}",
        ondelete="RESTRICT",
        use_alter=True,
    )
    model.__table__.append_constraint(constraint)
    EXTRA.append(str(AddConstraint(constraint).compile(dialect=dialect())))
    DOWN_EXTRA.append(f"ALTER TABLE {model.__table__.name} DROP CONSTRAINT {constraint.name}")
for table in ("audit_event", "event_outbox", "refresh_token_history", "quarantine"):
    EXTRA.append(
        f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
EXTRA += [
    "GRANT INSERT ON auth_session,refresh_token_history,auth_rate_limit,job,event_outbox,audit_event TO fleetiq_app",
    "GRANT UPDATE ON auth_session,auth_rate_limit TO fleetiq_app",
    "GRANT INSERT ON event_outbox,audit_event TO fleetiq_worker",
    "GRANT UPDATE (state,attempt,lease_owner,lease_expires_at,progress,error,result) ON job TO fleetiq_worker",
    "GRANT USAGE, SELECT ON SEQUENCE event_outbox_id_seq,audit_event_id_seq TO fleetiq_app,fleetiq_worker",
    "REVOKE SELECT ON auth_session,refresh_token_history,auth_rate_limit FROM fleetiq_worker",
    "REVOKE SELECT ON source_credential FROM fleetiq_app",
]
# freeze_migration grants SELECT after EXTRA; explicit revocations run after that grant.
POST_GRANTS = [
    "REVOKE SELECT ON auth_session,refresh_token_history,auth_rate_limit FROM fleetiq_worker",
    "REVOKE SELECT ON source_credential FROM fleetiq_app",
]


def enqueue_job(connection, row, *, actor_id, reason):
    """Persist request, replay event and audit together or roll back the entire write set."""
    with connection.begin_nested():
        job_id = connection.scalar(sa.insert(Job).values(**row).returning(Job.id))
        connection.execute(
            sa.insert(EventOutbox).values(
                organization_id=row["organization_id"],
                scope_kind="owner",
                scope_id=row["owner_id"],
                kind="job.pending",
                payload={"job_id": str(job_id)},
            )
        )
        connection.execute(
            sa.insert(AuditEvent).values(
                organization_id=row["organization_id"],
                actor_id=actor_id,
                action="job.enqueue",
                target_kind="job",
                target_id=job_id,
                scope_kind="owner",
                scope_id=row["owner_id"],
                versions={},
                reason=reason,
            )
        )
    return job_id
