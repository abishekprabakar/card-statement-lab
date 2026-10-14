"""Statement-engine and allocation tests. Every expected value is worked by
hand in the comment above the assertion -- none is copied from program output."""

import unittest
from decimal import Decimal as D

from cardlab import CardAccount, Segment, Txn, adb_interest, allocate_payment


def acct(*segments, **kw):
    return CardAccount(list(segments), **kw)


class AverageDailyBalance(unittest.TestCase):
    def test_hand_computed_cycle(self):
        # 30-day cycle, balance 1000 carried in (no grace), +500 purchase day 11,
        # -300 payment day 21:
        #   days 1-10  1000 * 10 = 10,000
        #   days 11-20 1500 * 10 = 15,000
        #   days 21-30 1200 * 10 = 12,000      sum 37,000, ADB 1,233.33
        #   interest = 37,000 * 0.24 / 365 = 24.3288 -> 24.33
        a = acct(Segment("purchase", "0.24", balance="1000", grace_eligible=True))
        st = a.run_cycle([Txn(11, "purchase", "500"), Txn(21, "payment", "300")])
        self.assertEqual(st.average_daily_balance["purchase"], D("1233.33"))
        self.assertEqual(st.interest, D("24.33"))
        self.assertEqual(st.new_balance, D("1224.33"))   # 1000+500-300+24.33
        # minimum: 1% of principal 1200 = 12.00, + interest 24.33 = 36.33 > 35 floor
        self.assertEqual(st.minimum_due, D("36.33"))
        self.assertFalse(st.grace_applied)

    def test_standalone_adb_function(self):
        # 15 days at 2000 + 16 days at 0, APR 18.25% -> DPR 0.0005
        # 30,000 * 0.0005 = 15.00
        self.assertEqual(adb_interest([2000] * 15 + [0] * 16, "0.1825"), D("15.00"))


class GracePeriod(unittest.TestCase):
    def test_new_account_purchases_are_interest_free(self):
        a = acct(Segment("purchase", "0.30", grace_eligible=True))
        st = a.run_cycle([Txn(1, "purchase", "800")])
        self.assertTrue(st.grace_applied)
        self.assertEqual(st.interest, D("0.00"))

    def test_paying_in_full_keeps_grace_partial_loses_it(self):
        a = acct(Segment("purchase", "0.1825", grace_eligible=True))
        a.run_cycle([Txn(1, "purchase", "800")])                 # statement 800
        st2 = a.run_cycle([Txn(20, "payment", "800"), Txn(22, "purchase", "100")])
        self.assertTrue(st2.paid_in_full and st2.grace_applied)
        self.assertEqual(st2.interest, D("0.00"))
        # Statement 2 balance is 100; pay only 40 by the due date -> no grace.
        # daily balances: days 1-9 100 (900), days 10-30 60 (21*60=1,260)
        # sum 2,160 * 0.0005 = 1.08
        st3 = a.run_cycle([Txn(10, "payment", "40")])
        self.assertFalse(st3.grace_applied)
        self.assertEqual(st3.interest, D("1.08"))

    def test_trailing_interest_then_grace_restored(self):
        # Carried balance 1000 (grace lost) paid in full on day 10.
        # Interest for days 1-9: 9 * 1000 * 0.0005 = 4.50; grace returns next cycle.
        a = acct(Segment("purchase", "0.1825", balance="1000", grace_eligible=True))
        st = a.run_cycle([Txn(10, "payment", "1000")])
        self.assertTrue(st.paid_in_full)
        self.assertFalse(st.grace_applied)
        self.assertEqual(st.interest, D("4.50"))
        st2 = a.run_cycle([Txn(3, "payment", "4.50"), Txn(5, "purchase", "200")])
        self.assertTrue(st2.grace_applied)
        self.assertEqual(st2.interest, D("0.00"))

    def test_cash_advance_never_gets_grace(self):
        # 100 cash advance on day 21 of a 30-day cycle at 36.5% (DPR 0.001):
        # 10 days * 100 * 0.001 = 1.00, even though purchases are in grace.
        a = acct(Segment("purchase", "0.20", grace_eligible=True), Segment("cash", "0.365"))
        st = a.run_cycle([Txn(21, "cash_advance", "100"), Txn(21, "purchase", "100")])
        self.assertTrue(st.grace_applied)
        self.assertEqual(st.interest_by_segment["purchase"], D("0.00"))
        self.assertEqual(st.interest_by_segment["cash"], D("1.00"))


class PaymentAllocation(unittest.TestCase):
    def segs(self):
        return [Segment("purchase", "0.1999", balance="2000"),
                Segment("cash", "0.2799", balance="500"),
                Segment("transfer", "0", balance="1500")]

    def test_minimum_lowest_first_excess_highest_first(self):
        # 600 paid against a 100 minimum: the 100 goes to the 0% transfer, the
        # 500 excess must go to the 27.99% cash balance first (1026.53(a)).
        s = self.segs()
        applied = allocate_payment(s, "600", "100")
        self.assertEqual(applied, {"transfer": D("100.00"), "cash": D("500.00")})
        self.assertEqual([x.balance for x in s], [D("2000.00"), D("0.00"), D("1400.00")])

    def test_excess_cascades_to_next_highest_apr(self):
        # 100 min -> transfer; 900 excess -> cash 500, then purchases 400.
        s = self.segs()
        allocate_payment(s, "1000", "100")
        self.assertEqual([x.balance for x in s], [D("1600.00"), D("0.00"), D("1400.00")])

    def test_payment_below_minimum_all_lowest_first(self):
        s = self.segs()
        allocate_payment(s, "60", "100")
        self.assertEqual(s[2].balance, D("1440.00"))
        self.assertEqual(s[1].balance, D("500.00"))

    def test_deferred_interest_window_takes_excess_first(self):
        # 1026.53(b)(1): in the last two cycles before a deferred-interest promo
        # expires, the excess goes to the promo ahead of higher-APR balances.
        s = [Segment("purchase", "0.24", balance="1000"),
             Segment("promo", "0", balance="800", deferred_interest=True,
                     promo_cycles_left=2, promo_apr_after="0.26")]
        allocate_payment(s, "535", "35")
        # min 35 -> promo (0% is lowest); excess 500 -> promo (window) first
        self.assertEqual(s[1].balance, D("265.00"))
        self.assertEqual(s[0].balance, D("1000.00"))
        # Outside the window the same excess would have gone to purchases.
        s2 = [Segment("purchase", "0.24", balance="1000"),
              Segment("promo", "0", balance="800", deferred_interest=True,
                      promo_cycles_left=3, promo_apr_after="0.26")]
        allocate_payment(s2, "535", "35")
        self.assertEqual((s2[0].balance, s2[1].balance), (D("500.00"), D("765.00")))

    def test_overpayment_becomes_credit_balance(self):
        # 100 carried in (no grace), 150 paid on day 5 -> credit of 50.
        # Interest accrues only on the 4 positive days: 4*100*0.20/365 = 0.219
        # -> 0.22, so the statement shows a credit of 50 - 0.22 = 49.78.
        a = acct(Segment("purchase", "0.20", balance="100", grace_eligible=True))
        st = a.run_cycle([Txn(5, "payment", "150")])
        self.assertEqual(st.interest_by_segment["purchase"], D("0.22"))
        self.assertEqual(st.new_balance, D("-49.78"))
        self.assertEqual(st.minimum_due, D("0.00"))
        # The credit absorbs the next purchase.
        st2 = a.run_cycle([Txn(3, "purchase", "30")])
        self.assertEqual(st2.new_balance, D("-19.78"))


class FeesAndPromotions(unittest.TestCase):
    def test_late_fee_capped_at_minimum_due(self):
        # 1026.52(b): a penalty fee may not exceed the dollar amount of the
        # violation. Minimum due 25 (floor 25) missed -> late fee 25, not 30.
        a = acct(Segment("purchase", "0", grace_eligible=True), min_floor="25")
        st1 = a.run_cycle([Txn(1, "purchase", "300")])
        self.assertEqual(st1.minimum_due, D("25.00"))
        st2 = a.run_cycle([])
        self.assertEqual(st2.late_fee, D("25.00"))
        st3 = a.run_cycle([Txn(5, "payment", "500")])   # pays late fee's min on time
        self.assertEqual(st3.late_fee, D("0.00"))

    def test_deferred_interest_springs_at_expiry(self):
        # 1200 at 0% deferred, post-promo APR 36.5% (DPR 0.001), 2 cycles of 30
        # days, no payments: accrued = 1200 * 30 * 0.001 * 2 = 72.00, charged
        # in full when the promo ends with a balance.
        a = acct(Segment("purchase", "0.2", grace_eligible=True),
                 Segment("promo", "0", balance="1200", deferred_interest=True,
                         promo_cycles_left=2, promo_apr_after="0.365"),
                 late_fee="0")
        a.run_cycle([])
        st = a.run_cycle([])
        self.assertEqual(st.deferred_interest_charged, D("72.00"))
        self.assertEqual(a.seg("promo").balance, D("1272.00"))
        self.assertEqual(a.seg("promo").apr, D("0.365"))

    def test_deferred_interest_waived_when_paid_off(self):
        a = acct(Segment("purchase", "0.2", grace_eligible=True),
                 Segment("promo", "0", balance="1200", deferred_interest=True,
                         promo_cycles_left=2, promo_apr_after="0.365"))
        a.run_cycle([])
        st = a.run_cycle([Txn(10, "payment", "1200")])
        self.assertEqual(st.deferred_interest_charged, D("0.00"))
        self.assertEqual(a.seg("promo").balance, D("0.00"))


if __name__ == "__main__":
    unittest.main()
