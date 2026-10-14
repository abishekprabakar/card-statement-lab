"""Balance segments and CARD Act payment allocation (Reg Z 12 CFR 1026.53).

A credit card balance is not one number. It is a set of *segments* --
purchases, cash advances, balance transfers, promotional balances -- each with
its own APR and its own grace-period treatment. How a payment is split across
them decides how much interest the cardholder pays next month.

Rules implemented here:

* The **required minimum payment** may be applied however the issuer likes.
  Issuers almost universally apply it to the *lowest* APR first, which is the
  order that maximises their interest income; we do the same.
* Any amount **in excess of the minimum** must go to the segment with the
  *highest* APR first, then the next highest, and so on (1026.53(a)).
* **Deferred-interest exception** (1026.53(b)(1)): during the two billing
  cycles immediately before a deferred-interest promotion expires, the excess
  must go to that promotional balance first, so the cardholder has a fair
  chance to clear it before the retroactive interest lands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def money(x) -> Decimal:
    """Coerce to Decimal and round to cents, half-up (the way statements round)."""
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Segment:
    """One balance bucket on the card.

    apr               -- annual percentage rate as a decimal (0.2499 = 24.99%)
    grace_eligible    -- purchases are; cash advances and most transfers are not
    deferred_interest -- True for "no interest if paid in full by ..." promos
    promo_cycles_left -- billing cycles remaining before a promo expires
                         (None for non-promotional segments)
    accrued_deferred  -- interest silently accruing on a deferred-interest
                         promo, charged in full if the balance survives expiry
    """

    name: str
    apr: Decimal
    balance: Decimal = Decimal("0.00")
    grace_eligible: bool = False
    deferred_interest: bool = False
    promo_cycles_left: int | None = None
    promo_apr_after: Decimal | None = None
    accrued_deferred: Decimal = field(default=Decimal("0"))

    def __post_init__(self) -> None:
        self.apr = Decimal(str(self.apr))
        self.balance = money(self.balance)
        if self.promo_apr_after is not None:
            self.promo_apr_after = Decimal(str(self.promo_apr_after))

    @property
    def in_deferred_window(self) -> bool:
        """True during the last two cycles before a deferred-interest promo ends."""
        return (
            self.deferred_interest
            and self.promo_cycles_left is not None
            and 1 <= self.promo_cycles_left <= 2
        )


def _pay_in_order(segments: list[Segment], amount: Decimal, order: list[Segment],
                  applied: dict[str, Decimal]) -> Decimal:
    """Apply `amount` to `order` front to back. Returns whatever is left over."""
    for seg in order:
        if amount <= 0:
            break
        take = min(max(seg.balance, Decimal("0")), amount)
        if take > 0:
            seg.balance -= take
            amount -= take
            applied[seg.name] = applied.get(seg.name, Decimal("0")) + take
    return amount


def allocate_payment(segments: list[Segment], payment, minimum_due) -> dict[str, Decimal]:
    """Apply a payment to the segments in place, CARD Act style.

    Returns {segment name: amount applied}. A payment larger than the total
    balance leaves the remainder unapplied (a credit balance); the leftover is
    reported under the key "__credit__".
    """
    payment = money(payment)
    minimum_due = money(minimum_due)
    if payment < 0:
        raise ValueError("payment must be non-negative")

    applied: dict[str, Decimal] = {}
    # Stable sorts: ties keep the caller's segment order, which makes results
    # deterministic and easy to reason about in tests.
    lowest_first = sorted(segments, key=lambda s: s.apr)
    highest_first = sorted(segments, key=lambda s: s.apr, reverse=True)

    # 1. The minimum: issuer's choice; lowest APR first.
    to_minimum = min(payment, minimum_due)
    left = _pay_in_order(segments, to_minimum, lowest_first, applied)

    # 2. The excess over the minimum: highest APR first, unless a deferred-
    #    interest promo is in its final two cycles, in which case it goes first.
    excess = payment - to_minimum + left
    window = [s for s in segments if s.in_deferred_window]
    order = window + [s for s in highest_first if s not in window]
    excess = _pay_in_order(segments, excess, order, applied)

    if excess > 0:
        applied["__credit__"] = excess
    return applied
