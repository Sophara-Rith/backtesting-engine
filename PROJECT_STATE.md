# Project State — MS Trend Matrix Python Port

Last updated: 2026-09-05 by Implementation of strategy.py Slice 3/4 (pending QA review)

## Module status
| Module | Status | Spec sections | Notes |
|---|---|---|---|
| events.py | implemented, QA-approved | port-spec §9, engine-spec §1 | dataclasses defined with ladder extensions + exit_tag threading |
| data_handler.py | implemented, QA-approved | port-spec §2 QA §15.2, engine-spec §2 | OANDA tick-volume NaN coercion, no look-ahead, strict current_time |
| strategy.py | slice 3/4 implemented, pending QA review (slice 4 pending) | port-spec §1-8, QA §15.3-15.8 | SL/TP computed (3 SL modes, 4 TP modes), locked §7 risk-distance fix, tp_prices list |
| portfolio.py | not started | port-spec §9, QA §15.7 | ladder partial-close logic |
| execution.py | not started | engine-spec §6 | |
| cost_model.py | not started | engine-spec §6 | |
| risk_manager.py | not started | engine-spec §7, port-spec §7 | |
| performance.py | not started | engine-spec §8, port-spec §9 (leg vs position aggregation) | |
| reporter.py | not started | engine-spec §9 | |
| engine.py | not started | engine-spec §10 | orchestrator, build last |
| config.py | not started | port-spec §10/§14, §11 (locked: every field must be here) | |
| run.py | not started | engine-spec §12 | |

## Infrastructure & Git History
- **2026-09-05**: Reconciled initial diverged branches on GitHub remote (`git@github.com:Sophara-Rith/backtesting-engine.git`). Renamed local `master` to `main`, pushed to replace the empty initial web-UI commit on `origin/main` via `--force-with-lease`, deleted redundant `origin/master`, and configured `main` to track `origin/main`.
- **2026-09-05**: Hardened `.gitignore` with comprehensive secret/credential exclusion patterns, verified no sensitive data or credentials exist in git history, and created `README.md`.

## Cross-module interface notes
- `SignalEvent.strength` on entry signals carries the per-unit SL distance in price terms (mode-appropriate), NOT a final position size/quantity (Strategy does not have access to equity). Downstream modules (`risk_manager.py` / `portfolio.py` / `engine.py`) divide `equity * (risk_per_trade_pct / 100)` by this distance to derive actual position quantity. It is `None` when `use_risk_sizing` is `False` or mode-appropriate distance is `<= 0`.

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
Awaiting QA review of strategy.py Slice 3/4 from the operator.
