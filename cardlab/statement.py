"""Billing-cycle engine: average daily balance interest, grace periods, deferred
interest promotions and late fees, one statement at a time.

Conventions (the common US issuer conventions, stated so they can be tested):

* A transaction dated day *d* posts at the start of day *d*; the day's balance
  is the end-of-day balance after all of that day's postings.
* Interest uses the **average daily balance (including new transactions)**
  method with a **daily periodic rate** of APR / 365:

      interest = (sum of daily balances) * APR / 365
               = ADB * (APR / 365) * days_in_cycle

  computed separately for every segment, then rounded to cents.
* **Grace period.** New purchases escape interest only if the account was in
  grace coming into the cycle *and* the previous statement balance is paid in
  full by its due date. Paying in full after losing grace restores it for the
  *next* cycle; the current cycle still carries "trailing" interest. Cash
  advances never get a grace period.
* **No double-cycle billing** (Reg Z 1026.54): interest is computed only on
  balances for days in the current cycle; nothing reaches back a cycle.
* **Deferred interest.** A 0% "no interest if paid in full" promotion accrues
  interest at its post-promo APR in the background. If any balance survives the
  final promo cycle, all of that accrued interest is charged at once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from .allocation import CENT, Segment, allocate_payment, money

DAYS_PER_YEAR = Decimal(365)


@dataclass
class Txn:
    day: int                 # 1-based day within the billing cycle
    kind: str                # purchase | cash_advance | payment | fee | refund
    amount: Decimal
    segment: str | None = None   # target segment for charges (default by kind)

    def __post_init__(self) -> None:
        self.amount = money(self.amount)
        if self.amount < 0:
            raise ValueError("amounts are positive; the kind sets the direction")
        if self.kind not in {"purchase", "cash_advance", "payment", "fee", "refund"}:
            raise ValueError(f"unknown transaction kind {self.kind!r}")


@dataclass
class Statement:
    cycle: int
    days: int
    previous_balance: Decimal
    payments: Decimal
    purchases: Decimal
    cash_advances: Decimal
    fees: Decimal
    interest: Decimal
    new_balance: Decimal
    minimum_due: Decimal
    paid_in_full: bool
    grace_applied: bool
    late_fee: Decimal
    deferred_interest_charged: Decimal
    average_daily_balance: dict[str, Decimal] = field(default_factory=dict)
    interest_by_segment: dict[str, Decimal] = field(default_factory=dict)
    segment_balances: dict[str, Decimal] = field(default_factory=dict)
    allocations: list[dict[str, Decimal]] = field(default_factory=list)


class CardAccount:
    """A card with several balance segments, run one billing cycle at a time."""

    def __init__(self, segments: list[Segment], *, min_floor="35", min_pct="0.01",
                 late_fee="30", due_day: int = 25,
                 promo_excluded_from_pay_in_full: bool = True) -> None:
        names = [s.name for s in segments]
        if len(set(names)) != len(names):
            raise ValueError("segment names must be unique")
        self.segments = segments
        self.min_floor = money(min_floor)
        self.min_pct = Decimal(str(min_pct))
        self.late_fee_amount = money(late_fee)
        self.due_day = due_day
        self.promo_excluded = promo_excluded_from_pay_in_full
        self.cycle = 0
        self.prev_statement_balance = money(sum(s.balance for s in segments))
        self.prev_minimum_due = Decimal("0.00")
        self.prev_pay_in_full_amount = self._pay_in_full_amount()
        self.in_grace = self.prev_pay_in_full_amount <= 0

    # ------------------------------------------------------------------ helpers
    def seg(self, name: str) -> Segment:
        for s in self.segments:
            if s.name == name:
                return s
        raise KeyError(name)

    @property
    def balance(self) -> Decimal:
        return money(sum(s.balance for s in self.segments))

    def _pay_in_full_amount(self) -> Decimal:
        """What must be paid by the due date to keep (or regain) the grace period.

        Most issuers let promotional balances ride: paying the statement balance
        *less* any promo balance keeps purchases interest-free. That is an issuer
        practice rather than a Reg Z mandate, so it is a switch.
        """
        owed = sum(s.balance for s in self.segments
                   if not (self.promo_excluded and s.promo_cycles_left is not None))
        return money(max(Decimal("0"), owed))

    def _default_segment(self, kind: str) -> str:
        if kind == "cash_advance":
            return "cash"
        return "purchase"

    def minimum_payment(self, balance: Decimal, interest: Decimal, fees: Decimal) -> Decimal:
        """1% of principal + this cycle's interest and fees, floored, capped at balance."""
        if balance <= 0:
            return Decimal("0.00")
        principal = balance - interest - fees
        formula = money(principal * self.min_pct + interest + fees)
        return min(balance, max(self.min_floor, formula))

    # -------------------------------------------------------------- the engine
    def run_cycle(self, txns: list[Txn], days: int = 30) -> Statement:
        self.cycle += 1
        for t in txns:
            if not 1 <= t.day <= days:
                raise ValueError(f"transaction day {t.day} outside a {days}-day cycle")
        by_day: dict[int, list[Txn]] = {}
        for t in txns:
            by_day.setdefault(t.day, []).append(t)

        previous_balance = self.balance
        sums = {s.name: Decimal("0") for s in self.segments}
        accruing = {s.name: Decimal("0") for s in self.segments}  # credit days excluded
        totals = {"payment": Decimal("0"), "purchase": Decimal("0"),
                  "cash_advance": Decimal("0"), "fee": Decimal("0")}
        paid_by_due = Decimal("0")
        min_remaining = self.prev_minimum_due
        allocations: list[dict[str, Decimal]] = []

        for day in range(1, days + 1):
            for t in by_day.get(day, []):
                if t.kind == "payment":
                    applied = allocate_payment(self.segments, t.amount, min_remaining)
                    allocations.append(applied)
                    if "__credit__" in applied:
                        # Overpayment: carried as a credit (negative balance)
                        # on the purchase segment, offsetting future charges.
                        self.seg("purchase").balance -= applied["__credit__"]
                    min_remaining = max(Decimal("0"), min_remaining - t.amount)
                    totals["payment"] += t.amount
                    if day <= self.due_day:
                        paid_by_due += t.amount
                elif t.kind == "refund":
                    # A refund is a credit to the segment the purchase sat in.
                    target = self.seg(t.segment or "purchase")
                    target.balance -= min(target.balance, t.amount)
                else:
                    target = self.seg(t.segment or self._default_segment(t.kind))
                    target.balance += t.amount
                    totals[t.kind] += t.amount
            for s in self.segments:
                sums[s.name] += s.balance
                accruing[s.name] += max(s.balance, Decimal("0"))

        # Grace: in grace coming in AND previous statement paid in full by due.
        paid_in_full = paid_by_due >= self.prev_pay_in_full_amount
        grace_applied = self.in_grace and paid_in_full

        interest_by_seg: dict[str, Decimal] = {}
        adb: dict[str, Decimal] = {}
        for s in self.segments:
            adb[s.name] = money(sums[s.name] / days)
            if s.deferred_interest and s.promo_apr_after is not None:
                # Promo APR applies on the face of it; the real APR accrues silently.
                s.accrued_deferred += accruing[s.name] * s.promo_apr_after / DAYS_PER_YEAR
            if s.grace_eligible and grace_applied:
                charge = Decimal("0.00")
            else:
                # Days spent in credit (negative balance) earn no interest.
                charge = money(accruing[s.name] * s.apr / DAYS_PER_YEAR)
            interest_by_seg[s.name] = charge
            s.balance += charge
        interest = money(sum(interest_by_seg.values()))

        # Late fee: the previous minimum not received by the due date.
        late_fee = Decimal("0.00")
        if self.prev_minimum_due > 0 and paid_by_due < self.prev_minimum_due:
            late_fee = min(self.late_fee_amount, self.prev_minimum_due)  # 1026.52(b)
            self.seg("purchase").balance += late_fee

        # Promotions tick down; deferred interest springs at expiry.
        deferred_charged = Decimal("0.00")
        for s in self.segments:
            if s.promo_cycles_left is None:
                continue
            s.promo_cycles_left -= 1
            if s.promo_cycles_left > 0:
                continue
            if s.deferred_interest and s.balance > 0:
                deferred_charged += money(s.accrued_deferred)
                s.balance += money(s.accrued_deferred)
            s.accrued_deferred = Decimal("0")
            if s.promo_apr_after is not None:
                s.apr = s.promo_apr_after
            s.deferred_interest = False
            s.promo_cycles_left = None

        fees = money(totals["fee"] + late_fee)
        new_balance = self.balance
        minimum = self.minimum_payment(new_balance, interest + deferred_charged, fees)

        stmt = Statement(
            cycle=self.cycle, days=days, previous_balance=previous_balance,
            payments=money(totals["payment"]), purchases=money(totals["purchase"]),
            cash_advances=money(totals["cash_advance"]), fees=fees,
            interest=money(interest + deferred_charged), new_balance=new_balance,
            minimum_due=minimum, paid_in_full=paid_in_full,
            grace_applied=grace_applied, late_fee=late_fee,
            deferred_interest_charged=deferred_charged,
            average_daily_balance=adb, interest_by_segment=interest_by_seg,
            segment_balances={s.name: s.balance for s in self.segments},
            allocations=allocations,
        )

        # Roll state forward to the next cycle.
        self.in_grace = paid_in_full
        self.prev_statement_balance = new_balance
        self.prev_pay_in_full_amount = self._pay_in_full_amount()
        self.prev_minimum_due = minimum
        return stmt


def adb_interest(daily_balances: list, apr) -> Decimal:
    """Stand-alone ADB interest for a list of end-of-day balances."""
    total = sum(Decimal(str(b)) for b in daily_balances)
    return (total * Decimal(str(apr)) / DAYS_PER_YEAR).quantize(CENT, rounding=ROUND_HALF_UP)
