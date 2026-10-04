"""Only declared compatible units are converted; pressure basis never inferred."""

UNITS = {
    "degC": ("temperature", 1.0, 0.0),
    "K": ("temperature", 1.0, -273.15),
    "degF": ("temperature", 5 / 9, -32 * 5 / 9),
    "kPa": ("pressure", 1.0, 0.0),
    "Pa": ("pressure", 0.001, 0.0),
    "bar": ("pressure", 100.0, 0.0),
    "mm/s": ("velocity", 1.0, 0.0),
    "m/s": ("velocity", 1000.0, 0.0),
}


def convert(value, raw_unit, canonical_unit, *, pressure_kind=None, raw_pressure_kind=None):
    if raw_unit not in UNITS or canonical_unit not in UNITS:
        raise ValueError("Missing or unknown unit")
    source, target = UNITS[raw_unit], UNITS[canonical_unit]
    if source[0] != target[0]:
        raise ValueError("Incompatible unit")
    if source[0] == "pressure" and (
        pressure_kind not in {"absolute", "gauge"} or raw_pressure_kind != pressure_kind
    ):
        raise ValueError("Missing or incompatible pressure basis")
    return (value * source[1] + source[2] - target[2]) / target[1]
