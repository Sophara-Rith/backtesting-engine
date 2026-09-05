"""
events.py — Event dataclasses (MarketEvent, SignalEvent, OrderEvent, FillEvent).

Governed by:
- backtest_engine_spec.md §1 (base event shapes)
- ms_matrix_python_port_spec.md §9, QA §15.1 (SignalEvent partial-close extension & Order/Fill tagging)
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


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
    direction: int = 0              # 1=long, -1=short, 0=flat/close
    strength: float = 1.0           # sizing hint (risk-based qty). ONLY meaningful on
                                    # ENTRY signals (direction != 0). Ignored on exit signals.
    close_fraction: Optional[float] = None
                                    # ONLY meaningful on EXIT signals (direction == 0):
                                    #   - 0.0-1.0  → close this fraction of the position's
                                    #     ORIGINAL entry quantity (used by T1/T2 ladder legs)
                                    #   - None     → close whatever quantity currently remains
                                    #     in the position (used by T3, SL, and EARLY exits)
                                    # On ENTRY signals (direction != 0) this field is unused
                                    # and must stay None.
    exit_tag: Optional[str] = None  # One of "T1" | "T2" | "T3" | "SL" | "EARLY".
                                    # ONLY set on exit signals. None on entry signals.
    sl_price: Optional[float] = None
                                    # ONLY meaningful on ENTRY signals: the stop-loss price
                                    # Portfolio should attach to the new Position. None on
                                    # exit signals (SL price doesn't change mid-trade in
                                    # this strategy - it's fixed at entry).
    tp_prices: Optional[List[float]] = None
                                    # ONLY meaningful on ENTRY signals: [tp1, tp2, tp3] for
                                    # ladder mode, or a single-element list [tp] for any
                                    # non-ladder TP mode, or None if the strategy handles
                                    # exits some other way. None on exit signals.
    timestamp: object = None


@dataclass
class OrderEvent:
    type: EventType = EventType.ORDER
    symbol: str = "XAUUSD"
    direction: int = 0              # 1=buy, -1=sell
    quantity: float = 1.0           # in lots (e.g. 1.0 = 100 oz)
    order_type: str = "MARKET"      # "MARKET" | "LIMIT" | "STOP"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    exit_tag: Optional[str] = None  # carried through from SignalEvent so Portfolio/Execution
                                    # can label the resulting Fill/trade_log row correctly
    timestamp: object = None


@dataclass
class FillEvent:
    type: EventType = EventType.FILL
    symbol: str = "XAUUSD"
    direction: int = 0
    quantity: float = 1.0
    fill_price: float = 0.0         # actual execution price (after spread/slippage)
    commission: float = 0.0
    swap: float = 0.0
    exit_tag: Optional[str] = None  # carried through for trade_log labeling
    timestamp: object = None
