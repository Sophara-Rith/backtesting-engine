# Changelog

All notable changes and accepted module implementations for the MS Trend Matrix Backtest Engine will be documented in this file.

Format: `YYYY-MM-DD` — `module_name`: spec sections implemented (notes/deviations).

---

- `2026-09-05` — `events.py`: Implemented event dataclasses per `backtest_engine_spec.md` §1 and `ms_matrix_python_port_spec.md` §9 (QA §15.1). Deviation/extension: threaded `exit_tag: Optional[str] = None` through `OrderEvent` and `FillEvent` in addition to `SignalEvent` to enable per-leg trade log labeling during ladder exits.
