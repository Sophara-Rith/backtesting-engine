# Project State — MS Trend Matrix Python Port

Last updated: 2026-09-05 by QA approval of events.py (starting data_handler.py)

## Module status
| Module | Status | Spec sections | Notes |
|---|---|---|---|
| events.py | implemented, QA-approved | port-spec §9, engine-spec §1 | dataclasses defined with ladder extensions + exit_tag threading |
| data_handler.py | not started | port-spec §2 QA §15.2, engine-spec §2 | |
| strategy.py | not started | port-spec §1-8, QA §15.3-15.8 | largest module, may need multiple sessions |
| portfolio.py | not started | port-spec §9, QA §15.7 | ladder partial-close logic |
| execution.py | not started | engine-spec §6 | |
| cost_model.py | not started | engine-spec §6 | |
| risk_manager.py | not started | engine-spec §7, port-spec §7 | |
| performance.py | not started | engine-spec §8, port-spec §9 (leg vs position aggregation) | |
| reporter.py | not started | engine-spec §9 | |
| engine.py | not started | engine-spec §10 | orchestrator, build last |
| config.py | not started | port-spec §10/§14, §11 (locked: every field must be here) | |
| run.py | not started | engine-spec §12 | |

## Locked decisions (do not re-litigate — see port-spec §13 for full detail)
1. Risk-sizing gate (port-spec §7): FIXED to check mode-appropriate distance, not
   always-dynamic. This is an intentional improvement over the Pine source.
2. Ladder exits (port-spec §9): Option A — SignalEvent extended with
   close_fraction/exit_tag/sl_price/tp_prices; Position tracks
   original_quantity + remaining_quantity; T1/T2 % is always of original_quantity.
3. Config (port-spec §11): every single Pine input must map to exactly one
   BacktestConfig field. Zero hardcoded literals in strategy.py.
4. Timeframe: M5. bars_per_year = 288 × 252.
5. Data: OANDA OHLC CSV, tick-count volume (expected/correct, not a bug).
6. Pivot confirmation lag (port-spec §2, §15.3): pivothigh/pivotlow confirm
   ms_len bars AFTER the pivot bar, not at it. This is the single highest-risk
   spot for silently introducing look-ahead bias — treat with extra care.

## Open items / blockers
(none yet — update as they arise)

## Next task
Implementation of data_handler.py.
