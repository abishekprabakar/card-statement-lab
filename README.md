# card-statement-lab

A credit card statement engine that does the parts of card math people get
wrong: **average-daily-balance interest per balance segment**, **grace periods
that are lost and regained**, **CARD Act payment allocation**, **deferred-interest
promotions that spring at expiry**, **penalty-fee caps**, and the **Reg Z
minimum-payment warning** printed on every US statement.

Pure Python standard library. Money is `Decimal`, rounded to cents half-up.

## What it models

| Rule | Where | Source |
|---|---|---|
| Interest = sum of daily balances x APR/365, per segment | `statement.py` | ADB (including new transactions) method |
| Purchases are interest-free only if the account was in grace **and** the previous statement was paid in full by the due date; paying in full after losing grace restores it next cycle (this cycle still carries trailing interest) | `statement.py` | common issuer terms; Reg Z 1026.54 (no double-cycle billing) |
| Cash advances never get a grace period | `statement.py` | |
| Minimum payment applied lowest APR first (issuer's choice); **excess over the minimum must go to the highest APR first** | `allocation.py` | Reg Z 1026.53(a) |
| Last two cycles before a deferred-interest promo expires: excess goes to the promo first | `allocation.py` | Reg Z 1026.53(b)(1) |
| Deferred interest accrues silently at the post-promo APR and is charged in full if any promo balance survives expiry | `statement.py` | |
| Late fee capped at the minimum payment that was missed | `statement.py` | Reg Z 1026.52(b) |
| Overpayments become a credit balance; credit days earn no interest | `statement.py` | |
| "Only the minimum payment" months and total, plus the 3-year payment and savings when payoff takes > 36 months | `disclosure.py` | Reg Z 1026.7(b)(12), Appendix M1 |

Minimum payment = 1% of principal + this cycle's interest and fees, with a
$35 floor, capped at the balance (all configurable).

One convention is a switch rather than a rule: by default promotional balances
do not have to be paid off to keep the grace period (`promo_excluded_from_pay_in_full=True`),
which is what most issuers do in practice.

## Layout

```
cardlab/allocation.py   Segment + CARD Act payment allocation
cardlab/statement.py    billing-cycle engine (ADB, grace, deferred interest, late fees)
cardlab/disclosure.py   Appendix M1 minimum-payment warning and 3-year payment
cardlab/demo.py         six statements of one cardholder
tests/                  21 unittest cases, expected values worked by hand
```

## Run it

```bash
python3 -m cardlab.demo                               # six statements + disclosures
python3 -m unittest discover -s tests -t . -v         # or: pytest
```

## How the tests are checked

Every expected value is derived independently, not snapshotted:

* ADB interest worked by hand: 1,000 x 10 days + 1,500 x 10 + 1,200 x 10 = 37,000;
  x 0.24/365 = **$24.33**.
* Trailing interest: 1,000 for 9 days at a 0.05% daily rate = **$4.50**, then grace returns.
* Allocation: $600 against a $100 minimum with 0% / 19.99% / 27.99% balances
  sends $100 to the 0% balance and the $500 excess to the 27.99% balance.
* Deferred interest: 1,200 x 30 days x 0.1% x 2 cycles = **$72.00** charged at expiry.
* Minimum-only payoff is checked against the closed-form annuity
  n = -ln(1 - rB/P) / ln(1 + r), and a two-phase (percentage, then floor)
  payoff against the geometric decay B·0.98^n followed by that annuity.
* The 3-year payment matches B·r / (1 - (1 + r)^-36) (1,000 at 12% -> 33.2143).

```
Ran 21 tests in 0.009s

OK
```

## Sample output (statements 4-6 of `python3 -m cardlab.demo`)

Statement 4 shows CARD Act allocation (the $35 minimum to the 0% promo, the
excess to cash at 29.99% then purchases at 23.99%) and trailing interest after
paying in full. Statement 5 is inside the deferred-interest window, so the
whole excess goes to the promo. In statement 6 the promo expires with a balance
and $178.07 of deferred interest lands at once.

```
=== Statement 4  (30-day cycle) ======================================
  Previous balance      $  2,286.37
  Payments            - $  1,200.00
  Purchases           + $     60.00
  Cash advances       + $      0.00
  Fees                + $      0.00
  Interest            + $      5.10
  New balance           $  1,151.47
  Minimum due           $     35.00
  Prev stmt paid in full by due date: True   grace applied: False
  segment        APR      ADB         interest    balance
  purchase      23.99%  $    146.35  $      2.89  $     62.89
  cash          29.99%  $     89.65  $      2.21  $      2.21
  furniture      0.00%  $  1,322.37  $      0.00  $  1,086.37
  payment 1 allocated: furniture=643.63, cash=244.51, purchase=311.86

=== Statement 5  (30-day cycle) ======================================
  Previous balance      $  1,151.47
  Payments            - $    500.00
  Purchases           + $    140.00
  Cash advances       + $      0.00
  Fees                + $      0.00
  Interest            + $      0.05
  New balance           $    791.52
  Minimum due           $     35.00
  Prev stmt paid in full by due date: True   grace applied: True
  segment        APR      ADB         interest    balance
  purchase      23.99%  $    184.22  $      0.00  $    202.89
  cash          29.99%  $      2.21  $      0.05  $      2.26
  furniture      0.00%  $    903.04  $      0.00  $    586.37
  payment 1 allocated: furniture=500.00

=== Statement 6  (30-day cycle) ======================================
  Previous balance      $    791.52
  Payments            - $    200.00
  Purchases           + $      0.00
  Cash advances       + $      0.00
  Fees                + $      0.00
  Interest            + $    182.13   (incl. $178.07 deferred interest)
  New balance           $    773.65
  Minimum due           $    188.05
  Prev stmt paid in full by due date: False   grace applied: False
  segment        APR      ADB         interest    balance
  purchase      23.99%  $    202.89  $      4.00  $    206.89
  cash          29.99%  $      2.26  $      0.06  $      2.32
  furniture     26.99%  $    513.04  $      0.00  $    564.44
  payment 1 allocated: furniture=200.00

Statement 6 minimum-payment warning
Minimum Payment Warning: If you make only the minimum payment each
period, you will pay more in interest and it will take you longer to
pay off your balance.

  If you make no additional   You will pay off the    And you will end up
  charges and each month      balance shown on this   paying an estimated
  you pay...                  statement in about...   total of...
  Only the minimum payment    3 years                 $1,079

For comparison: a $3,000 balance at 21.99% (the 3-year row is required
because minimum payments alone would take longer than 36 months)
Minimum Payment Warning: If you make only the minimum payment each
period, you will pay more in interest and it will take you longer to
pay off your balance.

  If you make no additional   You will pay off the    And you will end up
  charges and each month      balance shown on this   paying an estimated
  you pay...                  statement in about...   total of...
  Only the minimum payment    12 years                $7,004
  $115                        3 years                 $4,140 (Savings=$2,864)
```

## License

MIT
