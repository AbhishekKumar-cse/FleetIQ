"""Stable content hashes include timestamps; duplicate IDs cannot mutate evidence."""

import hashlib
import json
import math
from datetime import datetime
from uuid import UUID

from fleetiq_data.quality.time import utc


def raw_json(value):
    if isinstance(value, dict):
        return {str(k): raw_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [raw_json(v) for v in value]
    if isinstance(value, datetime):
        return utc(value).isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return {"$nonfinite_float": repr(value)}
    return value


def content_hash(payload):
    encoded = json.dumps(
        raw_json(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class DuplicateConflict(ValueError):
    pass


def remember(receipts, event_id, payload):
    checksum = content_hash(payload)
    previous = receipts.get(event_id)
    if previous is not None:
        if previous != checksum:
            raise DuplicateConflict("Event ID has different content")
        return False
    receipts[event_id] = checksum
    return True
