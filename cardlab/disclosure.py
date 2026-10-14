"""Minimum-payment warning disclosures (Reg Z 1026.7(b)(12), Appendix M1).

Every periodic statement must tell the cardholder, roughly:

    If you make only the minimum payment each period, you will pay off the
    balance shown on this statement in about N months and end up paying an
    estimated total of $X.

    If you make no additional charges and pay $P each month, you will pay off
    the balance in 3 years and end up paying an estimated total of $Y
    (savings = $X - $Y).

Appendix M1's assumptions, which this module follows:

* no further charges, fees or payments other than the minimum;
* a *monthly* periodic rate of APR / 12;
* when segments carry different APRs, the minimum payment is applied to the
  lowest APR first (the issuer's own allocation for the minimum);
* the months figure is a whole number of months, totals round to the dollar;
* the 3-year payment uses the balance-weighted APR, rounded to the dollar, and
  is disclosed only when minimum payments would take longer than 36 months.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .allocation import Segment, allocate_payment, money

TWELVE = Decimal(12)
DOLLAR = Decimal("1")


def to_dollar(x: Decimal) -> Decimal:
    return Decimal(x).quantize(DOLLAR, rounding=ROUND_HALF_UP)


@dataclass
class Repayment:
    months: int
    total_paid: Decimal          # exact, to the cent
    total_interest: Decimal

    @property
    def total_paid_rounded(self) -> Decimal:
        return to_dollar(self.total_paid)


@dataclass
class Disclosure:
    balance: Decimal
    minimum_only: Repayment
    show_36_month: bool
    payment_36: Decimal | None
    total_36: Decimal | None
    savings_36: Decimal | None

    def render(self) -> str:
        m = self.minimum_only
        years = m.months / 12
        lines = [
            "Minimum Payment Warning: If you make only the minimum payment each",
            "period, you will pay more in interest and it will take you longer to",
            "pay off your balance.",
            "",
            f"  {'If you make no additional':<28}{'You will pay off the':<24}{'And you will end up'}",
            f"  {'charges and each month':<28}{'balance shown on this':<24}{'paying an estimated'}",
            f"  {'you pay...':<28}{'statement in about...':<24}{'total of...'}",
            f"  {'Only the minimum payment':<28}{(f'{m.months} month' + ('s' if m.months != 1 else '') if m.months < 24 else f'{years:.0f} years'):<24}"
            f"${m.total_paid_rounded:,.0f}",
        ]
        if self.show_36_month:
            lines.append(
                f"  {f'${self.payment_36:,.0f}':<28}{'3 years':<24}"
                f"${self.total_36:,.0f} (Savings=${self.savings_36:,.0f})"
            )
        return "\n".join(lines)


def minimum_payment(balance: Decimal, interest: Decimal, floor: Decimal, pct: Decimal) -> Decimal:
    """Same formula as the statement engine: pct of principal + interest, floored."""
    if balance <= 0:
        return Decimal("0.00")
    formula = money((balance - interest) * pct + interest)
    return min(balance, max(floor, formula))


def simulate_minimum_only(segments: list[Segment], floor="35", pct="0.01",
                          max_months: int = 1200) -> Repayment:
    """Pay only the minimum, month by month, until the balance reaches zero."""
    segs = copy.deepcopy(segments)
    floor, pct = money(floor), Decimal(str(pct))
    months = 0
    paid = Decimal("0.00")
    interest_total = Decimal("0.00")
    for s in segs:
        s.balance = max(s.balance, Decimal("0.00"))
    while sum(s.balance for s in segs) > 0:
        months += 1
        if months > max_months:
            raise RuntimeError("minimum payments never retire this balance")
        interest = Decimal("0.00")
        for s in segs:
            charge = money(s.balance * s.apr / TWELVE)
            s.balance += charge
            interest += charge
        balance = money(sum(s.balance for s in segs))
        pay = minimum_payment(balance, interest, floor, pct)
        # Only the minimum is paid, so the whole payment follows the
        # issuer's lowest-APR-first rule for the minimum.
        allocate_payment(segs, pay, minimum_due=pay)
        paid += pay
        interest_total += interest
    return Repayment(months=months, total_paid=paid, total_interest=interest_total)


def weighted_apr(segments: list[Segment]) -> Decimal:
    total = sum(s.balance for s in segments)
    if total == 0:
        return Decimal("0")
    return sum(s.balance * s.apr for s in segments) / total


def amortizing_payment(balance, apr, months: int) -> Decimal:
    """Level payment that retires `balance` in `months` at monthly rate apr/12."""
    balance, apr = Decimal(str(balance)), Decimal(str(apr))
    if apr == 0:
        return balance / months
    r = apr / TWELVE
    return balance * r / (1 - (1 + r) ** -months)


def build_disclosure(segments: list[Segment], floor="35", pct="0.01") -> Disclosure:
    balance = money(sum(s.balance for s in segments))
    rep = simulate_minimum_only(segments, floor, pct)
    if rep.months <= 36:
        return Disclosure(balance, rep, False, None, None, None)
    p36 = to_dollar(amortizing_payment(balance, weighted_apr(segments), 36))
    total36 = p36 * 36
    return Disclosure(balance, rep, True, p36, total36,
                      rep.total_paid_rounded - total36)
