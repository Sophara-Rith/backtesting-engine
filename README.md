# MS Trend Matrix — XAUUSD Backtesting Engine

An event-driven backtesting engine for XAUUSD (Gold), implementing an exact Python port of the "MS Trend Matrix — 5-Gate Strategy" Pine Script. Designed to overcome TradingView Essential tier limitations (10,000 bar history cap, lack of native multi-parameter sweeps, and absence of custom broker cost modeling) by providing an offline, modular, and realistic simulation environment.

## Setup

1. **Python Environment:** Requires Python 3.10+ (configured with Python 3.13):
   ```bash
   python -m venv venv
   # On Windows:
   .\venv\Scripts\activate
   # On Linux/macOS:
   source venv/bin/activate
   ```

2. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
   *(Dependencies are strictly pinned to standard quantitative libraries: `pandas`, `numpy`, and `matplotlib`).*

## Architecture & Specifications

Before making any modifications to `backtest/`, review the authoritative specification documents in `docs/`:
- [`docs/backtest_engine_spec.md`](docs/backtest_engine_spec.md) — Architectural specification, event lifecycle, component interfaces, and XAUUSD transaction cost models.
- [`docs/ms_matrix_python_port_spec.md`](docs/ms_matrix_python_port_spec.md) — Strategy port specification, 5-gate conditions, SL/TP modes, ladder exit logic, and QA checklist.

## Current Status

See [`PROJECT_STATE.md`](PROJECT_STATE.md) for the active status of each module, locked architectural decisions, and current progress.

## Data

The `data/` directory is git-ignored. Place your OANDA M5 XAUUSD CSV export (with columns `time,open,high,low,close,volume`) directly into `data/` before executing backtests.
