from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal


def money(value: str) -> Decimal:
    amount = Decimal(value)
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Amount must be finite and positive")
    if amount >= Decimal("100000000000000") or amount.as_tuple().exponent < -4:
        raise ValueError("Amount exceeds NUMERIC(18,4)")
    return amount


def usd(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def stay_dates(check_in: date, check_out: date):
    if check_out <= check_in:
        raise ValueError("Invalid stay interval")
    return [check_in + timedelta(days=i) for i in range((check_out - check_in).days)]


def shared_quantity(plan_pool_ids: list[str], pool_quantities: dict[str, int | None]):
    pools = set(plan_pool_ids)
    if len(pools) != 1:
        raise ValueError("Independent or unknown pools must not be aggregated")
    quantity = pool_quantities[next(iter(pools))]
    if quantity is not None and (type(quantity) is not int or quantity < 0):
        raise ValueError("Invalid inventory quantity")
    return quantity
