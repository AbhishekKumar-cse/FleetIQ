"""Serialized assets and half-open installation windows."""

from fleetiq_domain.models.schema import entity

Component = entity(
    "component",
    {
        "serial": "text:required",
        "part_id": "uuid:optional",
        "kind": "text:required",
        "manufacture_date": "date:optional",
    },
    unique=(("serial",),),
)
Engine = entity(
    "engine",
    {"component_id": "uuid:required", "engine_type": "text:required"},
    refs={"component_id": "component"},
    primary=("component_id",),
)
Installation = entity(
    "installation",
    {
        "aircraft_id": "uuid:required",
        "component_id": "uuid:required",
        "position": "text:required",
        "installed_at": "time:required",
        "removed_at": "time:optional",
        "initial_hours": "number:required:0",
        "initial_cycles": "int:required:0",
    },
    refs={"aircraft_id": "aircraft", "component_id": "component"},
    checks=(
        "removed_at IS NULL OR removed_at > installed_at",
        "initial_hours >= 0 AND initial_cycles >= 0",
    ),
)

EXCLUSIONS = [
    f"ALTER TABLE installation ADD CONSTRAINT installation_no_{name}_overlap EXCLUDE USING gist "
    f"(organization_id WITH =, {keys}, tstzrange(installed_at, removed_at, '[)') WITH &&)"
    for name, keys in (
        ("component", "component_id WITH ="),
        ("position", "aircraft_id WITH =, position WITH ="),
    )
]
