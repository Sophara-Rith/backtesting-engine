"""
strategy.py — MS Trend Matrix 5-Gate Strategy port and indicator state.

Governed by:
- backtest_engine_spec.md §3 (Strategy class structure, on_bar, on_fill)
- ms_matrix_python_port_spec.md §1 (Strategy state), §2 (Indicator porting), QA §15.3

Status: Slice 1 of 4 (State & Indicators) implemented.
"""

from collections import deque
from typing import Any, Optional
import pandas as pd

try:
    from .events import FillEvent, MarketEvent, SignalEvent
except ImportError:
    from events import FillEvent, MarketEvent, SignalEvent


class _Bar:
    """Lightweight internal container for OHLC bar buffer."""
    __slots__ = ("index", "open", "high", "low", "close", "volume", "timestamp")

    def __init__(self, index: int, open: float, high: float, low: float, close: float, volume: float, timestamp: object):
        self.index = index
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.timestamp = timestamp


class Strategy:
    def __init__(self, config: Any):
        self.config = config

        # --- Market structure state (Pine `var`) ---
        self.ph_val: Optional[float] = None      # last CONFIRMED pivot high price
        self.pl_val: Optional[float] = None      # last CONFIRMED pivot low price
        self.ph_idx: Optional[int] = None        # bar index of that pivot (reference only)
        self.pl_idx: Optional[int] = None
        self.direction: Optional[bool] = None    # True=bull, False=bear, None=undetermined (before first ChoCh)
        self.trend_entry_price: Optional[float] = None
        self.current_target: Optional[float] = None
        self.trend_start_idx: Optional[int] = None
        self.t1_level: Optional[float] = None
        self.t2_level: Optional[float] = None
        self.t3_level: Optional[float] = None

        # --- Daily trade cap tracking ---
        self.trades_today: int = 0
        self.last_trade_day = None               # date of the last bar processed, for day-change detection

        # --- Active position levels (populated at entry, used by exits - slices 3/4) ---
        self.active_sl: Optional[float] = None
        self.active_tp1: Optional[float] = None
        self.active_tp2: Optional[float] = None
        self.active_tp3: Optional[float] = None
        self.active_entry: Optional[float] = None

        # --- Bar counter (needed for pivot index bookkeeping) ---
        self.bar_index: int = -1   # incremented to 0 on the first on_bar call

        # --- OHLC ring buffer for pivot detection + 1-bar-back lookups ---
        # size = 2*ms_len + 2 per port-spec §1's recommendation
        buffer_size = 2 * self.config.ms_len + 2
        self.bar_buffer: deque = deque(maxlen=buffer_size)

        # --- EMA state ---
        self.ema55: Optional[float] = None

        # --- ATR (Wilder) state ---
        self.atr: Optional[float] = None
        self._prev_close_for_tr: Optional[float] = None   # needed for True Range calc

        # --- VWAP state (resets on session/day change) ---
        self._vwap_cum_pv: float = 0.0      # cumulative sum(price * volume)
        self._vwap_cum_vol: float = 0.0     # cumulative sum(volume)
        self.vwap: Optional[float] = None
        self._vwap_session_key = None       # tracks current session/day, to detect reset boundary
        self.vwap_history: deque = deque(maxlen=self.config.vwap_slope_lookback + 1)

        # --- ADX/DMI (Wilder) state ---
        self._prev_high: Optional[float] = None
        self._prev_low: Optional[float] = None
        self._smoothed_plus_dm: Optional[float] = None
        self._smoothed_minus_dm: Optional[float] = None
        self._smoothed_tr: Optional[float] = None
        self._smoothed_dx: Optional[float] = None   # second-stage smoothing for ADX itself
        self.adx: Optional[float] = None
        self.plus_di: Optional[float] = None
        self.minus_di: Optional[float] = None

    def on_bar(self, event: MarketEvent) -> Optional[SignalEvent]:
        """
        Consume a MarketEvent, update indicator buffers and internal state.
        Returns None unconditionally in Slice 1 (no gate/entry/exit evaluation yet).
        """
        self._update_indicators(event)
        return None

    def on_fill(self, event: FillEvent) -> None:
        """
        Called when a fill executes (optional callback for future slices).
        """
        pass

    def _update_indicators(self, event: MarketEvent) -> None:
        """
        Update all indicator states and buffers sequentially:
        1. Ring buffer & bar counter
        2. EMA (length = self.config.ema_len)
        3. True Range & ATR (Wilder, length = self.config.atr_length)
        4. VWAP & slope history
        5. ADX / DMI (Wilder two-stage, di_length & adx_smoothing)
        6. Pivot high/low detection with ms_len confirmation lag
        """
        self.bar_index += 1
        bar = _Bar(
            index=self.bar_index,
            open=event.open,
            high=event.high,
            low=event.low,
            close=event.close,
            volume=event.volume,
            timestamp=event.timestamp,
        )
        self.bar_buffer.append(bar)

        # --- 2. EMA ---
        ema_len = float(self.config.ema_len)
        alpha_ema = 2.0 / (ema_len + 1.0)
        if self.ema55 is None:
            self.ema55 = event.close
        else:
            self.ema55 = alpha_ema * event.close + (1.0 - alpha_ema) * self.ema55

        # --- 3. True Range & ATR (Wilder) ---
        if self._prev_close_for_tr is None:
            tr = event.high - event.low
        else:
            tr = max(
                event.high - event.low,
                abs(event.high - self._prev_close_for_tr),
                abs(event.low - self._prev_close_for_tr),
            )
        self._prev_close_for_tr = event.close

        atr_len = float(self.config.atr_length)
        if self.atr is None:
            self.atr = tr
        else:
            self.atr = (self.atr * (atr_len - 1.0) + tr) / atr_len

        # --- 4. VWAP & slope history ---
        ts = event.timestamp
        if hasattr(ts, "date"):
            current_session_key = ts.date()
        else:
            current_session_key = pd.to_datetime(ts).date()

        if self._vwap_session_key != current_session_key:
            self._vwap_cum_pv = 0.0
            self._vwap_cum_vol = 0.0
            self._vwap_session_key = current_session_key

        hlc3 = (event.high + event.low + event.close) / 3.0
        self._vwap_cum_pv += hlc3 * event.volume
        self._vwap_cum_vol += event.volume

        if self._vwap_cum_vol > 0.0:
            self.vwap = self._vwap_cum_pv / self._vwap_cum_vol
        else:
            self.vwap = hlc3

        self.vwap_history.append(self.vwap)

        # --- 5. ADX / DMI (Wilder Two-Stage) ---
        if self._prev_high is not None and self._prev_low is not None:
            up_move = event.high - self._prev_high
            down_move = self._prev_low - event.low

            if up_move > down_move and up_move > 0.0:
                plus_dm = up_move
            else:
                plus_dm = 0.0

            if down_move > up_move and down_move > 0.0:
                minus_dm = down_move
            else:
                minus_dm = 0.0
        else:
            plus_dm = 0.0
            minus_dm = 0.0

        di_len = float(self.config.di_length)
        if self._smoothed_plus_dm is None:
            self._smoothed_plus_dm = plus_dm
            self._smoothed_minus_dm = minus_dm
            self._smoothed_tr = tr
        else:
            self._smoothed_plus_dm = (self._smoothed_plus_dm * (di_len - 1.0) + plus_dm) / di_len
            self._smoothed_minus_dm = (self._smoothed_minus_dm * (di_len - 1.0) + minus_dm) / di_len
            self._smoothed_tr = (self._smoothed_tr * (di_len - 1.0) + tr) / di_len

        if self._smoothed_tr and self._smoothed_tr > 0.0:
            self.plus_di = 100.0 * (self._smoothed_plus_dm / self._smoothed_tr)
            self.minus_di = 100.0 * (self._smoothed_minus_dm / self._smoothed_tr)
        else:
            self.plus_di = 0.0
            self.minus_di = 0.0

        di_sum = self.plus_di + self.minus_di
        if di_sum > 0.0:
            dx = 100.0 * abs(self.plus_di - self.minus_di) / di_sum
        else:
            dx = 0.0

        adx_len = float(self.config.adx_smoothing)
        if self._smoothed_dx is None:
            self._smoothed_dx = dx
            self.adx = dx
        else:
            self._smoothed_dx = (self._smoothed_dx * (adx_len - 1.0) + dx) / adx_len
            self.adx = self._smoothed_dx

        self._prev_high = event.high
        self._prev_low = event.low

        # --- 6. Pivot High / Low with ms_len confirmation lag ---
        ms_len = self.config.ms_len
        window_size = 2 * ms_len + 1
        if len(self.bar_buffer) >= window_size:
            window = list(self.bar_buffer)[-window_size:]
            candidate_idx = ms_len
            candidate = window[candidate_idx]

            # Check pivot high (strictly highest in window)
            is_pivot_high = True
            for i, b in enumerate(window):
                if i != candidate_idx and b.high >= candidate.high:
                    is_pivot_high = False
                    break

            if is_pivot_high:
                self.ph_val = candidate.high
                self.ph_idx = candidate.index

            # Check pivot low (strictly lowest in window)
            is_pivot_low = True
            for i, b in enumerate(window):
                if i != candidate_idx and b.low <= candidate.low:
                    is_pivot_low = False
                    break

            if is_pivot_low:
                self.pl_val = candidate.low
                self.pl_idx = candidate.index
