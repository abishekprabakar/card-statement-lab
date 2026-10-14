"""Minimum-payment disclosure tests, checked against closed-form annuity math
computed independently in floating point (not against the simulator itself)."""

import math
import unittest
from decimal import Decimal as D

from cardlab import Segment, amortizing_payment, build_disclosure, simulate_minimum_only
from cardlab.disclosure import weighted_apr


def closed_form_months(balance, apr, payment):
    """Months to retire `balance` with a level payment: n = -ln(1 - rB/P)/ln(1+r)."""
    r = apr / 12
    return math.ceil(-math.log(1 - r * balance / payment) / math.log(1 + r))


class AmortizingPayment(unittest.TestCase):
    def test_textbook_value(self):
        # 1000 over 36 months at 12% APR (1%/month): 10 / (1 - 1.01**-36) = 33.2143
        self.assertAlmostEqual(float(amortizing_payment(1000, "0.12", 36)), 33.2143, places=4)

    def test_zero_apr(self):
        self.assertEqual(amortizing_payment(720, "0", 36), D(20))


class MinimumOnlySimulation(unittest.TestCase):
    def test_flat_payment_matches_annuity_closed_form(self):
        # pct = 0 makes the minimum a flat $50 floor (interest at 1%/month on
        # <=1000 never exceeds 10). Closed form: n = -ln(1-0.2)/ln(1.01) = 22.43
        # -> 23 payments. The 22 full payments leave
        #   B22 = 1000*1.01**22 - 50*(1.01**22 - 1)/0.01 = 21.14
        # and the last payment is 21.14 * 1.01 = 21.35, total about 1121.35.
        rep = simulate_minimum_only([Segment("p", "0.12", balance="1000")], floor="50", pct="0")
        self.assertEqual(rep.months, closed_form_months(1000, 0.12, 50))
        self.assertEqual(rep.months, 23)
        b22 = 1000 * 1.01 ** 22 - 50 * (1.01 ** 22 - 1) / 0.01
        expected_total = 22 * 50 + b22 * 1.01
        self.assertAlmostEqual(float(rep.total_paid), expected_total, delta=0.05)
        self.assertAlmostEqual(float(rep.total_interest), expected_total - 1000, delta=0.05)

    def test_two_phase_percentage_then_floor(self):
        # Minimum = 2% of principal + interest, floor $25, APR 18% (1.5%/month).
        # Phase 1: each payment retires exactly 2% of the balance, so
        #   B_n = 5000 * 0.98**n  while  B_n*(0.02 + 0.015) >= 25,
        #   i.e. while B_n >= 714.29 -> n1 = ceil(ln(714.29/5000)/ln 0.98).
        # Phase 2: a flat $25 annuity on B_n1 (closed form above).
        B0, r, pct, floor = 5000.0, 0.18, 0.02, 25.0
        threshold = floor / (pct + r / 12)
        n1 = math.ceil(math.log(threshold / B0) / math.log(1 - pct))
        b1 = B0 * (1 - pct) ** n1
        expected = n1 + closed_form_months(b1, r, floor)
        rep = simulate_minimum_only([Segment("p", "0.18", balance="5000")], floor="25", pct="0.02")
        # Cent rounding each month can move the boundary by one payment.
        self.assertLessEqual(abs(rep.months - expected), 1, (rep.months, expected))
        self.assertGreater(rep.months, 120)   # a ten-plus-year payoff, as the warning says

    def test_minimum_goes_to_lowest_apr_first(self):
        # With only minimums paid, the 0% balance is retired first and the 29.99%
        # balance compounds: the same total balance costs more interest than a
        # single blended segment would at the weighted APR.
        split = [Segment("cash", "0.2999", balance="1000"), Segment("promo", "0", balance="1000")]
        blended = [Segment("all", str(weighted_apr(split)), balance="2000")]
        self.assertAlmostEqual(float(weighted_apr(split)), 0.14995, places=6)
        a = simulate_minimum_only(split)
        b = simulate_minimum_only(blended)
        self.assertGreater(a.total_interest, b.total_interest)


class Disclosure(unittest.TestCase):
    def test_36_month_row_and_savings(self):
        # 3000 at 21.99%: minimum-only takes far longer than 3 years, so the
        # 3-year row is required. Payment = round(annuity(3000, 21.99%, 36)).
        d = build_disclosure([Segment("p", "0.2199", balance="3000")])
        r = 0.2199 / 12
        p = 3000 * r / (1 - (1 + r) ** -36)                     # 114.56...
        self.assertTrue(d.show_36_month)
        self.assertEqual(d.payment_36, D(round(p)))
        self.assertEqual(d.total_36, D(round(p)) * 36)
        self.assertEqual(d.savings_36, d.minimum_only.total_paid_rounded - d.total_36)
        self.assertGreater(d.savings_36, 0)
        text = d.render()
        self.assertIn("3 years", text)
        self.assertIn(f"${round(p)}", text)

    def test_short_payoff_omits_36_month_row(self):
        # 300 at 20%: minimum is the $35 floor -> about 10 months, < 36.
        d = build_disclosure([Segment("p", "0.20", balance="300")])
        self.assertFalse(d.show_36_month)
        self.assertEqual(d.minimum_only.months, closed_form_months(300, 0.20, 35))
        self.assertIn(f"{d.minimum_only.months} months", d.render())


if __name__ == "__main__":
    unittest.main()
