# Production-Grade Event-Driven Backtesting Engine — XAUUSD

## Overview

Build a local Python backtesting engine that replicates a Pine Script strategy
on XAUUSD CSV data. The engine must be event-driven, modular, and capable of
running on unlimited bar counts with realistic cost modeling.

**Target:** Replace TradingView Essential (10K bar cap, no optimization, no
custom costs) with a fully offline, parameter-sweepable engine.

---

## File Structure

backtest/
├── init.py
├── events.py # All event dataclasses
├── data_handler.py # CSV loader + bar iterator
├── strategy.py # Ported Pine Script logic (user fills this)
├── portfolio.py # Position tracking, PnL, equity
├── execution.py # Broker simulator, fill logic
├── cost_model.py # Spread, commission, swap
├── risk_manager.py # Position sizing, drawdown stop
├── performance.py # Metrics computation
├── reporter.py # Charts, trade log, summary
├── engine.py # Orchestrator / main loop
├── config.py # All tunable parameters in one place
└── run.py # Entry point


---

## 1. Events (`events.py`)

Define these as `dataclass` types. Every component communicates ONLY through
these events — no direct method calls between components.

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

class EventType(Enum):
    MARKET = "MARKET"
    SIGNAL = "SIGNAL"
    ORDER = "ORDER"
    FILL = "FILL"

@dataclass
class MarketEvent:
    type: EventType = EventType.MARKET
    timestamp: object = None       # pd.Timestamp
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0

@dataclass
class SignalEvent:
    type: EventType = EventType.SIGNAL
    symbol: str = "XAUUSD"
    direction: int = 0             # 1=long, -1=short, 0=flat/close
    strength: float = 1.0          # optional: position sizing hint
    timestamp: object = None

@dataclass
class OrderEvent:
    type: EventType = EventType.ORDER
    symbol: str = "XAUUSD"
    direction: int = 0             # 1=buy, -1=sell
    quantity: float = 1.0          # in lots (e.g. 1.0 = 100 oz)
    order_type: str = "MARKET"     # "MARKET" | "LIMIT" | "STOP"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    timestamp: object = None

@dataclass
class FillEvent:
    type: EventType = EventType.FILL
    symbol: str = "XAUUSD"
    direction: int = 0
    quantity: float = 1.0
    fill_price: float = 0.0        # actual execution price (after spread)
    commission: float = 0.0
    swap: float = 0.0
    timestamp: object = None

2. Data Handler (data_handler.py)
Responsibility: Load CSV, expose one bar at a time, control the timeline.

class DataHandler:
    def __init__(self, csv_path: str, symbol: str = "XAUUSD"):
        """
        - Read CSV with pandas
        - Expected columns: time, open, high, low, close, volume
        - Parse 'time' column as datetime, set as index
        - Sort by time ascending
        - Store as self.df
        - Initialize self.current_index = 0
        """
        pass

    def get_bar(self) -> Optional[MarketEvent]:
        """
        Return MarketEvent for current bar, or None if exhausted.
        Increments internal index.
        """
        pass

    def has_more(self) -> bool:
        """Return True if more bars remain."""
        pass

    def current_time(self) -> object:
        """Return timestamp of the bar just delivered."""
        pass

CSV format expected:

time,open,high,low,close,volume
2024-01-01 00:00:00,2023.50,2025.10,2022.80,2024.30,15234

Key rules:

No look-ahead: only bar i is visible when processing bar i.
Must handle missing volume (XAUUSD often has 0 or NaN volume).
Must handle timezone-naive timestamps (assume UTC).
3. Strategy (strategy.py)
Responsibility: Consume MarketEvent, emit SignalEvent. This is where
the user ports their Pine Script.

class Strategy:
    def __init__(self, symbol: str = "XAUUSD"):
        """
        - Store symbol
        - Initialize indicator state (SMA buffers, RSI deque, etc.)
        - Do NOT load the full dataframe — process bar-by-bar
        """
        pass

    def on_bar(self, event: MarketEvent) -> Optional[SignalEvent]:
        """
        Called once per bar.
        1. Update indicators with event.close (and OHLC if needed)
        2. Evaluate entry/exit conditions
        3. Return SignalEvent or None

        Pine Script mapping:
          strategy.entry("L", strategy.long)   → return SignalEvent(direction=1)
          strategy.entry("S", strategy.short)  → return SignalEvent(direction=-1)
          strategy.close("L")                  → return SignalEvent(direction=0)
          no condition met                     → return None
        """
        pass

    def on_fill(self, event: FillEvent):
        """
        Optional: called when a fill executes. Useful for strategies that
        track entry price for trailing stops or ATR-based exits.
        """
        pass

Porting guide (include as comments in the file):

Pine Script	Python
ta.sma(source, length)	collections.deque + running sum, or pandas.Series.rolling on a growing list
ta.rsi(source, length)	Wilder's smoothing: alpha = 1/length
ta.crossover(a, b)	a_prev <= b_prev and a_curr > b_curr
ta.crossunder(a, b)	a_prev >= b_prev and a_curr < b_curr
ta.ema(source, length)	alpha = 2/(length+1), recursive
ta.atr(length)	Wilder's ATR on true range
ta.highest(source, length)	max(deque of last N values)
ta.lowest(source, length)	min(deque of last N values)
barstate.islast	Not needed — engine handles end-of-data

Critical rule: The strategy must NOT access future bars. It only sees
the current MarketEvent and its own internal state.

4. Portfolio (portfolio.py)
Responsibility: Track positions, cash, equity. Convert SignalEvent
→ OrderEvent.

class Portfolio:
    def __init__(self, initial_cash: float = 10_000.0,
                 symbol: str = "XAUUSD"):
        """
        - self.cash = initial_cash
        - self.positions = {}  # symbol → Position dataclass
        - self.equity_curve = []  # list of (timestamp, equity)
        - self.trade_log = []     # completed trades
        """
        pass

    @dataclass
    class Position:
        symbol: str
        direction: int          # 1 or -1
        quantity: float         # in lots
        entry_price: float
        entry_time: object
        stop_price: Optional[float] = None
        take_profit: Optional[float] = None

    def on_signal(self, event: SignalEvent) -> Optional[OrderEvent]:
        """
        Logic:
        - If signal.direction == 0 (close):
            if in position → emit OrderEvent to close
        - If signal.direction != 0 (enter/reverse):
            if in opposite position → emit close OrderEvent first
            emit new OrderEvent with direction and quantity
        - If no change → return None

        Quantity determined by RiskManager (see below).
        """
        pass

    def on_fill(self, event: FillEvent):
        """
        - Update position (open, close, or reverse)
        - On close: compute realized PnL, append to trade_log
        - Update cash
        """
        pass

    def mark_to_market(self, price: float, timestamp):
        """
        - Compute unrealized PnL
        - Append (timestamp, total_equity) to equity_curve
        """
        pass

    def get_equity(self) -> float:
        """Return cash + unrealized PnL."""
        pass

PnL calculation (XAUUSD):

pnl = (exit_price - entry_price) × direction × quantity × 100
# 1 lot = 100 oz of gold
# direction: +1 for long, -1 for short

5. Execution Handler (execution.py)
Responsibility: Simulate the broker. Consume OrderEvent, emit FillEvent.

class ExecutionHandler:
    def __init__(self, cost_model: "CostModel"):
        self.cost_model = cost_model
        self.pending_orders = []  # for LIMIT/STOP orders

    def on_order(self, event: OrderEvent, current_price: float) -> Optional[FillEvent]:
        """
        - MARKET orders: fill immediately at current_price ± spread/2
        - LIMIT orders: check if bar's high/low crossed the limit price
        - STOP orders: check if bar's high/low crossed the stop price

        For XAUUSD:
          Buy fill price  = current_price + spread/2
          Sell fill price = current_price - spread/2

        Return FillEvent with actual fill_price, commission, swap.
        """
        pass

    def check_pending(self, bar: MarketEvent) -> list[FillEvent]:
        """
        At each bar, check if any pending LIMIT/STOP orders were triggered.
        Use bar.high and bar.low for the check.
        """
        pass

Fill assumptions:

Market orders fill at close of the signal bar + spread adjustment.
If both a stop and take-profit are hit in the same bar, assume worst case (stop fills first).
No partial fills (simplification for XAUUSD retail).
6. Cost Model (cost_model.py)
Responsibility: All transaction costs in one place.

@dataclass
class CostModel:
    """
    XAUUSD retail parameters (adjust to your broker):
    """
    spread: float = 0.30            # in price units (e.g. $0.30 per oz)
    commission_per_lot: float = 3.50  # USD per lot per side
    swap_long: float = -2.50        # USD per lot per day (long)
    swap_short: float = 1.20        # USD per lot per day (short)
    slippage: float = 0.05          # extra adverse price move per side

    def get_execution_price(self, base_price: float, direction: int) -> float:
        """
        direction=1 (buy):  return base_price + spread/2 + slippage
        direction=-1 (sell): return base_price - spread/2 - slippage
        """
        pass

    def get_commission(self, quantity_lots: float) -> float:
        """Return commission_per_lot × quantity_lots"""
        pass

    def get_swap(self, direction: int, quantity_lots: float,
                 days_held: float) -> float:
        """
        Return (swap_long or swap_short) × quantity_lots × days_held
        """
        pass

Why this matters for XAUUSD:

Spread is the dominant cost on short-hold strategies (scalping).
Swap dominates on long-hold strategies (swing/position).
Without realistic costs, backtest PnL is inflated 20–40%.
7. Risk Manager (risk_manager.py)
Responsibility: Determine position size, enforce drawdown limits.

class RiskManager:
    def __init__(self, risk_per_trade: float = 0.01,
                 max_drawdown: float = 0.20,
                 max_position_lots: float = 5.0):
        """
        - risk_per_trade: fraction of equity risked per trade (1% = 0.01)
        - max_drawdown: halt trading if equity drops 20% from peak
        - max_position_lots: hard cap on position size
        """
        pass

    def position_size(self, equity: float, entry_price: float,
                      stop_price: float) -> float:
        """
        risk_amount = equity × risk_per_trade
        stop_distance = |entry_price - stop_price|
        quantity = risk_amount / (stop_distance × 100)  # 100 oz per lot
        return min(quantity, max_position_lots)
        """
        pass

    def check_drawdown(self, equity: float, peak_equity: float) -> bool:
        """
        Return True if trading should HALT.
        (equity - peak_equity) / peak_equity < -max_drawdown
        """
        pass

    def can_trade(self, equity: float, peak_equity: float) -> bool:
        """Return False if drawdown limit breached."""
        pass

8. Performance Analyst (performance.py)
Responsibility: Compute all metrics from the equity curve and trade log.

class Performance:
    @staticmethod
    def compute(equity_curve: list, trades: list,
                bars_per_year: int = 252 * 24) -> dict:
        """
        bars_per_year: adjust for your timeframe
          1-min  → 1440 × 252
          5-min → 288 × 252
          15-min→ 96 × 252
          1-hr  → 24 × 252
          4-hr  → 6 × 252
          1-day → 252

        Returns dict with:
          - total_return: (final - initial) / initial
          - cagr: compound annual growth rate
          - sharpe_ratio: mean(returns) / std(returns) × sqrt(bars_per_year)
          - sortino_ratio: mean(returns) / std(downside_returns) × sqrt(bars_per_year)
          - max_drawdown: (peak - trough) / peak
          - max_drawdown_duration: bars in longest drawdown
          - win_rate: wins / total_trades
          - profit_factor: gross_profit / gross_loss
          - avg_trade_pnl: mean of per-trade PnL
          - avg_win / avg_loss
          - expectancy: win_rate × avg_win - (1-win_rate) × avg_loss
          - total_trades: count
          - exposure: fraction of bars in a position
        """
        pass

9. Reporter (reporter.py)
Responsibility: Generate visual and tabular output.

class Reporter:
    @staticmethod
    def print_summary(metrics: dict):
        """Pretty-print all metrics to console."""
        pass

    @staticmethod
    def plot_equity_curve(equity_curve: list, save_path: str = "equity.png"):
        """
        matplotlib plot:
          - Line: equity over time
          - Shaded area: drawdown periods
          - Horizontal line: initial capital
        """
        pass

    @staticmethod
    def plot_trades(df: pd.DataFrame, save_path: str = "trades.png"):
        """
        Bar chart of per-trade PnL (green=win, red=loss).
        Cumulative PnL line overlaid.
        """
        pass

    @staticmethod
    def export_trades_csv(trades: list, save_path: str = "trades.csv"):
        """
        Columns: entry_time, exit_time, direction, quantity,
                 entry_price, exit_price, pnl, holding_bars
        """
        pass

10. Engine / Orchestrator (engine.py)
Responsibility: The main loop. Wires everything together.

class BacktestEngine:
    def __init__(self, config: "BacktestConfig"):
        """
        - Instantiate all components from config
        - Create event queue (collections.deque)
        """
        pass

    def run(self):
        """
        Main loop:
        while data_handler.has_more():
            1. bar = data_handler.get_bar()
            2. Push MarketEvent to queue
            3. strategy.on_bar(bar) → maybe SignalEvent
            4. portfolio.on_signal(signal) → maybe OrderEvent
            5. execution.on_order(order, bar.close) → FillEvent
            6. portfolio.on_fill(fill)
            7. risk_manager.check_drawdown(...)
            8. portfolio.mark_to_market(bar.close, bar.timestamp)

        After loop:
          - Force-close any open position
          - Compute performance metrics
          - Generate report
        """
        pass

Simplification note: For a single-symbol, single-strategy engine, you
can skip a literal event queue and just pass events as method arguments.
The "event-driven" pattern is preserved in the interface design
(each component only knows its input event type and output event type),
not in a message bus. This keeps the code readable.

11. Config (config.py)
@dataclass
class BacktestConfig:
    # Data
    csv_path: str = "data/xauusd_1h.csv"
    symbol: str = "XAUUSD"
    timeframe: str = "1h"          # for bars_per_year calc

    # Capital
    initial_cash: float = 10_000.0

    # Costs
    spread: float = 0.30
    commission_per_lot: float = 3.50
    swap_long: float = -2.50
    swap_short: float = 1.20
    slippage: float = 0.05

    # Risk
    risk_per_trade: float = 0.01
    max_drawdown: float = 0.20
    max_position_lots: float = 5.0

    # Strategy params (user fills these)
    # e.g. sma_fast: int = 10
    # e.g. sma_slow: int = 50
    # e.g. atr_length: int = 14
    # e.g. atr_multiplier: float = 2.0

12. Entry Point (run.py)
from config import BacktestConfig
from engine import BacktestEngine

if __name__ == "__main__":
    config = BacktestConfig(
        csv_path="data/xauusd_1h.csv",
        initial_cash=10_000,
        spread=0.30,
    )
    engine = BacktestEngine(config)
    engine.run()

Acceptance Criteria
Runs end-to-end on a sample XAUUSD 1H CSV without errors.
No look-ahead bias — strategy only sees current + past bars.
Costs applied correctly — verify by hand on first 3 trades.
Drawdown halt works — set max_drawdown=0.01 on a bad strategy,
confirm trading stops.
Equity curve is continuous — no gaps, no NaN.
Trade log matches — count of trades in log == count of fills.
Parameter sweep runnable — a simple for loop over sma_fast
values produces a table of Sharpe ratios.
Example: Parameter Sweep (bonus, in run.py)
import itertools

results = []
for fast, slow in itertools.product(range(5, 30), range(30, 100)):
    config = BacktestConfig(sma_fast=fast, sma_slow=slow, ...)
    engine = BacktestEngine(config)
    metrics = engine.run()
    results.append({"fast": fast, "slow": slow,
                    "sharpe": metrics["sharpe_ratio"],
                    "max_dd": metrics["max_drawdown"],
                    "trades": metrics["total_trades"]})

results_df = pd.DataFrame(results).sort_values("sharpe", ascending=False)
print(results_df.head(20))

Constraints & Assumptions
Single symbol (XAUUSD) — no multi-asset support needed.
Single strategy — no portfolio of strategies.
Market orders only in v1 (LIMIT/STOP in the execution handler are
stubs for v2).
No intrabar path simulation — assume fill at close ± spread.
(Good enough for 1H+ timeframes; for 1-min you'd want OHLC path logic.)
Python 3.10+, only pandas, numpy, matplotlib, dataclasses.
No external backtesting frameworks.

---

This file gives Claude everything it needs: the architecture, every class signature, the XAUUSD-specific cost math, the Pine Script mapping, and acceptance cri