"""The two pieces of statistics detection uses, exact and from first principles.

Both are small enough to check by hand, which is the point: a flag that says a unit stands
out has to be explainable down to the arithmetic, the same way a dollar in the baseline is.
No statistics library is used.

**1. Is this unit's rate higher than its comparison's?** (`binomial_upper_tail`)

Two groups, each with some number of events and some amount of exposure (employee-years).
If the two had the same underlying rate, then of all the events between them, each one
would fall in the first group with probability equal to that group's share of the exposure.
So the count in the first group, given the total, follows a binomial distribution, and the
chance of seeing a count at least as large as the one observed is its upper tail. This is
the standard exact test for comparing two rates. It makes no approximation, so it is as
valid for 3 events as for 300, and it allows for the comparison group's own uncertainty: a
comparison built on four events cannot make anything look significant.

**2. Many units are compared at once.** (`benjamini_hochberg`)

Compare forty units and two will look unusual at the 1-in-20 level by chance alone. The
Benjamini-Hochberg procedure adjusts each unit's result for the number of comparisons, so
that among the units finally flagged, the expected share that are chance findings is held
at a chosen level.

**3. Did this unit change more than everyone else did?** (`shifted_upper_tail`)

Two periods, and two groups: the unit and its comparison. Put every action of both groups
in one pile, and say `later` of them fell in the later period. If the unit had changed just
as the comparison did, the unit's actions would be no more likely to be "later" ones than
anyone else's. The number of the unit's actions that are later then follows a
hypergeometric distribution (the one behind Fisher's exact test), and the chance of a
number at least as large as the one seen is its upper tail.

One allowance is needed. If the unit's staffing grew between the periods while the
comparison's did not, more of its actions would be later ones for that reason alone. The
`odds` argument carries that: how much more the unit's staffing grew than the
comparison's. With equal staffing changes it is 1 and this is Fisher's test exactly.
"""

from __future__ import annotations

import math


def binomial_upper_tail(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p), summed exactly.

    Terms are worked out in logarithms so that large counts neither overflow nor vanish.
    """
    if not 0 <= k <= n:
        raise ValueError(f"need 0 <= k <= n, got k={k}, n={n}")
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"p must be a probability, got {p}")
    if k == 0:
        return 1.0
    if p == 0.0:
        return 0.0
    if p == 1.0:
        return 1.0
    log_p, log_q = math.log(p), math.log1p(-p)
    log_n = math.lgamma(n + 1)
    terms = [
        log_n - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * log_p + (n - i) * log_q
        for i in range(k, n + 1)
    ]
    largest = max(terms)
    total = math.exp(largest) * math.fsum(math.exp(t - largest) for t in terms)
    return min(max(total, 0.0), 1.0)


def benjamini_hochberg(p_values: list[float]) -> list[float]:
    """Each p-value adjusted for the number of comparisons, in the order given.

    Sort the p-values; the i-th smallest of m becomes p * m / i; then each is lowered to the
    smallest adjusted value at or after it, so the results never run out of order. A unit
    is flagged when its adjusted value is at or below the chosen false discovery rate.
    """
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        index = order[rank - 1]
        running = min(running, p_values[index] * m / rank)
        adjusted[index] = running
    return adjusted


def shifted_upper_tail(k: int, group: int, later: int, total: int, odds: float = 1.0) -> float:
    """P(X >= k), where X is how many of a group's actions fall in the later period.

    `total` actions in all, `later` of them in the later period, `group` of them belonging
    to the group. Each way of placing x of the group's actions among the later ones has
    weight C(group, x) * C(total - group, later - x) * odds**x; the chance is the weight at
    or above k over the weight of every possible x. Worked out in logarithms.
    """
    if not (0 <= group <= total and 0 <= later <= total):
        raise ValueError(f"need group and later within total, got {group}, {later}, {total}")
    if odds <= 0:
        raise ValueError(f"odds must be positive, got {odds}")
    lowest, highest = max(0, group + later - total), min(group, later)
    if not lowest <= k <= highest:
        if k < lowest:
            return 1.0
        raise ValueError(f"k={k} is not possible: at most {highest}")
    others = total - group
    log_odds = math.log(odds)

    def log_choose(n: int, r: int) -> float:
        return math.lgamma(n + 1) - math.lgamma(r + 1) - math.lgamma(n - r + 1)

    terms = {
        x: log_choose(group, x) + log_choose(others, later - x) + x * log_odds
        for x in range(lowest, highest + 1)
    }
    largest = max(terms.values())
    every = math.fsum(math.exp(t - largest) for t in terms.values())
    upper = math.fsum(math.exp(t - largest) for x, t in terms.items() if x >= k)
    return min(max(upper / every, 0.0), 1.0)
