"""Six billing cycles of one cardholder, printed as statements.

    python -m cardlab.demo

The story: a 0% deferred-interest furniture promo, everyday purchases, one cash
advance, a missed due date, then a recovery to paying in full -- chosen so that
every rule in the engine fires at least once.
"""

from __future__ import annotations

from decimal import Decimal

from .allocation import Segment
from .disclosure import build_disclosure
from .statement import CardAccount, Txn


def fmt(x: Decimal) -> str:
    return f"${x:>10,.2f}"


def print_statement(st, acct: CardAccount) -> None:
    print(f"\n=== Statement {st.cycle}  ({st.days}-day cycle) " + "=" * 38)
    print(f"  Previous balance      {fmt(st.previous_balance)}")
    print(f"  Payments            - {fmt(st.payments)}")
    print(f"  Purchases           + {fmt(st.purchases)}")
    print(f"  Cash advances       + {fmt(st.cash_advances)}")
    print(f"  Fees                + {fmt(st.fees)}" + ("   (late fee)" if st.late_fee else ""))
    print(f"  Interest            + {fmt(st.interest)}"
          + (f"   (incl. ${st.deferred_interest_charged:,.2f} deferred interest)"
             if st.deferred_interest_charged else ""))
    print(f"  New balance           {fmt(st.new_balance)}")
    print(f"  Minimum due           {fmt(st.minimum_due)}")
    print(f"  Prev stmt paid in full by due date: {st.paid_in_full}   "
          f"grace applied: {st.grace_applied}")
    print("  segment        APR      ADB         interest    balance")
    for s in acct.segments:
        print(f"  {s.name:<12} {float(s.apr) * 100:6.2f}%  {fmt(st.average_daily_balance[s.name])}"
              f"  {fmt(st.interest_by_segment[s.name])}  {fmt(st.segment_balances[s.name])}")
    for i, a in enumerate(st.allocations, 1):
        parts = ", ".join(f"{k}={v:.2f}" for k, v in a.items())
        print(f"  payment {i} allocated: {parts}")


def main() -> None:
    acct = CardAccount([
        Segment("purchase", "0.2399", grace_eligible=True),
        Segment("cash", "0.2999"),
        Segment("furniture", "0", deferred_interest=True,
                promo_cycles_left=6, promo_apr_after="0.2699"),
    ], due_day=25)

    cycles = [
        # 1: open the account, buy a sofa on the promo, everyday spend.
        [Txn(2, "purchase", "1800.00", "furniture"),
         Txn(5, "purchase", "420.50"), Txn(18, "purchase", "96.25")],
        # 2: pay the statement balance less the promo -> grace kept.
        [Txn(10, "purchase", "250.00"), Txn(20, "payment", "600.00"),
         Txn(22, "cash_advance", "300.00")],
        # 3: miss the due date entirely -> late fee, interest on everything.
        [Txn(8, "purchase", "75.00"), Txn(28, "payment", "100.00")],
        # 4: a big payment: minimum lowest-APR first, excess highest-APR first.
        [Txn(12, "payment", "1200.00"), Txn(15, "purchase", "60.00")],
        # 5: last two cycles before the promo ends -> excess goes to the promo,
        #    ahead of the higher-APR purchase and cash balances.
        [Txn(5, "purchase", "140.00"), Txn(20, "payment", "500.00")],
        # 6: promo expires with a balance left -> deferred interest springs.
        [Txn(20, "payment", "200.00")],
    ]
    for txns in cycles:
        st = acct.run_cycle(txns)
        print_statement(st, acct)

    print("\nStatement 6 minimum-payment warning")
    print(build_disclosure(acct.segments).render())

    print("\nFor comparison: a $3,000 balance at 21.99% (the 3-year row is required")
    print("because minimum payments alone would take longer than 36 months)")
    print(build_disclosure([Segment("purchase", "0.2199", balance="3000")]).render())


if __name__ == "__main__":
    main()
