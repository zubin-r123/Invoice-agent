"""Human-readable number formatting for rule messages and prose. Presentation only — never used
in comparisons or decisions."""

from decimal import ROUND_HALF_UP, Decimal


def _group_indian(digits: str) -> str:
    if len(digits) <= 3:
        return digits
    last3, rest = digits[-3:], digits[:-3]
    groups = []
    while len(rest) > 2:
        groups.insert(0, rest[-2:])
        rest = rest[:-2]
    if rest:
        groups.insert(0, rest)
    return ",".join(groups) + "," + last3


def format_inr(value: Decimal | None) -> str:
    """e.g. Decimal('4738000.5') -> '₹47,38,000.50'"""
    if value is None:
        return "—"
    negative = value < 0
    value = abs(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    whole, cents = divmod(value, 1)
    cents = int((cents * 100).to_integral_value(rounding=ROUND_HALF_UP))
    sign = "-" if negative else ""
    return f"{sign}₹{_group_indian(str(int(whole)))}.{cents:02d}"


def format_qty(value: Decimal | None) -> str:
    """e.g. Decimal('1000') -> '1,000'; Decimal('12.5') -> '12.5'"""
    if value is None:
        return "—"
    negative = value < 0
    value = abs(value)
    if value == value.to_integral_value():
        grouped = _group_indian(str(int(value)))
    else:
        whole, frac = str(value).split(".")
        grouped = f"{_group_indian(whole)}.{frac}"
    return f"{'-' if negative else ''}{grouped}"
