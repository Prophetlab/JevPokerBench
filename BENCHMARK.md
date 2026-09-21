# Benchmark methodology

JevPokerBench compares decision systems in no-limit Texas Hold'em. Cash games and sit-and-go tournaments have separate rankings. The engine, model adapters, scoring and web application are open source under MIT. Connected model weights and hosted APIs have their own licenses and access terms; this repository does not claim to reproduce proprietary models.

## Information and actions

Every actor receives its own hole cards, public board in dealing order, seat positions, stacks, bets, pot eligibility, legal actions and public action history/statistics. Opponents' hidden cards, future cards, random seeds and other models' decision probabilities are excluded. Model identities are replaced by seat aliases in the observation.

The engine supplies legal choices for each decision. These include check or call, fold when facing a bet, the minimum legal raise, and legal size options. Preflop candidates are 2, 2.5, 3 and 4 times the current bet or big blind. Postflop candidates are one-third, half, three-quarters, one and one-and-a-half pots. All-in is included where legal. Duplicate sizes are merged, invalid raises are removed, and amounts respect the chip unit. “3-bet” describes a raise's place in the betting sequence, not a separate action type.

Closed models use the official System One Adapter's prompt/schema and validation. Invalid responses receive bounded retries. Formal benchmark failures pause the affected run rather than inventing a model decision; resumes reuse accepted cached decisions. Personal tables instead check or fold on model errors/timeouts to keep the game moving, so their results do not enter the benchmark standings.

## Cash games

The default personal cash format starts each seat with 10,000 virtual units in total funds, buys in for 200, and uses blinds of 0.5/1. New personal tables cash out when any active player has at least 2,000 on-table units at a completed-hand boundary, returning every active stack to reserve before buying in for 200 again. Retired players never return. Existing matches retain their saved policy unless an explicit rule change is recorded; the formal cash benchmark records its threshold cutover hand and every reset. Legacy periodic 500-hand settlements are preserved in history. A player unable to afford the required buy-in leaves; surviving players continue. Rebuys transfer existing funds and never count as profit. No unlimited bankroll replenishment or rake is applied.

Rank by cumulative net profit, including both on-table and reserve funds. The run's configured hand limit and actual completed count are displayed. Every full dealer orbit is followed by a random seat shuffle. Half-unit cash chips apply from the denomination cutover onward; legacy fractions remain in reserve and historical results are preserved.

## SNG series

The default format starts each seat with 20,000 chips and increases blinds every 200 hands using the configured schedule. A completed event ranks players by elimination order. An N-seat event awards N−1 points to first place, down to zero for last place. Series points sum completed events only; equal totals share a rank. For ten players this is 9 through 0 points. A hand-limit stop is not silently treated as a completed tournament.

The series target and completed count are shown separately from the current event. A live leader is not presented as the final champion.

## Equity and timing

The dashed chart line is an **all-in runout adjustment**, not full strategic EV. Once all betting has ended with cards still to come, the evaluator calculates expected payouts for eligible main/side pots and replaces that runout's realized luck component. It enumerates up to 20,000 possible runouts; larger spaces use 5,000 deterministic Monte Carlo samples. Noneligible hands retain actual profit. Fractional expected values are valid even when physical chips use half units. Tournament chip equity is not rank EV or prize EV.

Decision timing includes network and upstream waiting, but excludes application admission waiting. Timing tables use executed successful decisions, exclude retries, and apply the documented 1.5×IQR outlier rule once a model has at least eight samples. These are observations of a specific deployment, not universal model speed claims.

## Interpreting results

Finite poker samples remain noisy. All-in adjustment removes only the covered runout luck; it does not prove optimal decisions or calibrated probabilities. Report sample sizes, rules, model/provider versions, adapter settings and any interrupted or excluded runs when publishing comparisons. Raw operational logs, credentials, account data and private room records are excluded from source releases. Review [MODEL_AUDIT.md](MODEL_AUDIT.md) for routing evidence and [TEST_REPORT.md](TEST_REPORT.md) for application verification.
