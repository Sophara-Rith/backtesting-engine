"""
strategy.py — MS Trend Matrix 5-Gate Strategy port and indicator state.

Governed by:
- backtest_engine_spec.md §3 (Strategy class structure, on_bar, on_fill)
- ms_matrix_python_port_spec.md §1 (State table), §2 (Indicator porting),
  §3 (5 gates & ChoCh), §4 (Trigger candles & wick-source fix), §5 (SL calculation),
  §6 (TP calculation & ladder target sequencing), §7 (Risk-sizing distance fix)
- QA checklist §15.3, §15.4, §15.5, §15.6

Status: Slices 1, 2 & 3 of 4 implemented (State, Indicators, ChoCh, 5 Gates, Entry Signals, SL/TP & Risk Sizing Distance).
"""

from collections import deque
from datetime import datetime, time, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo
import pandas as pd

try:
    from .events import EventType, FillEvent, MarketEvent, SignalEvent
except ImportError:
    from events import EventType, FillEvent, MarketEvent, SignalEvent


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


def _is_time_in_session(t: time, session_str: str) -> bool:
    """
    Check if a datetime.time falls within [start, end) for a session formatted 'HHMM-HHMM'.
    Assumes same-day session windows (per port-spec §2 v1 scope).
    """
    if not session_str or "-" not in session_str:
        return False
    parts = session_str.split("-")
    if len(parts) != 2:
        return False
    start_str, end_str = parts[0].strip(), parts[1].strip()
    start_time = time(int(start_str[:2]), int(start_str[2:4]))
    end_time = time(int(end_str[:2]), int(end_str[2:4]))

    if start_time <= end_time:
        return start_time <= t < end_time
    else:
        # Cross-midnight window support for completeness
        return t >= start_time or t < end_time


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
        self.last_trade_day = None               # date of the last bar processed (in session_timezone)

        # --- Flat / in-position state (managed fully in slice 4; checked here for flat-only entry) ---
        self.in_position: bool = False

        # --- Active position levels (populated at entry, used by exits - slices 3/4) ---
        self.active_sl: Optional[float] = None
        self.active_tp1: Optional[float] = None
        self.active_tp2: Optional[float] = None
        self.active_tp3: Optional[float] = None
        self.active_entry: Optional[float] = None

        # --- Trigger wick references (computed unconditionally every bar for slice 3 SL) ---
        self.trig_wick_low_bull: Optional[float] = None
        self.trig_wick_high_bear: Optional[float] = None

        # --- Bar counter (needed for pivot index bookkeeping) ---
        self.bar_index: int = -1   # incremented to 0 on the first on_bar call

        # --- OHLC ring buffer for pivot detection + 1-bar-back lookups ---
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
        Called once per bar:
        1. Capture previous-bar state (for crossover/crossunder and trigger comparisons)
        2. Update all indicators and ring buffers
        3. Evaluate ChoCh and update trend direction & ladder targets if direction changed
        4. Evaluate trigger candle patterns and wick references
        5. Evaluate 5 gates + session time filter + daily limit
        6. Compute Stop Loss prices across all 3 modes
        7. Compute Take Profit prices across all 4 modes (including ladder targets)
        8. Compute risk-sizing distance (§7 locked fix)
        9. Emit SignalEvent on entry conditions if flat
        """
        # 1. Capture state before this bar's indicator updates
        prev_bar = self.bar_buffer[-1] if len(self.bar_buffer) > 0 else None
        prev_ph_val = self.ph_val
        prev_pl_val = self.pl_val
        previous_direction = self.direction

        # 2. Update indicators and ring buffer
        self._update_indicators(event)

        close = event.close
        open_price = event.open
        high = event.high
        low = event.low

        prev_close = prev_bar.close if prev_bar is not None else None
        prev_open = prev_bar.open if prev_bar is not None else open_price
        prev_high = prev_bar.high if prev_bar is not None else high
        prev_low = prev_bar.low if prev_bar is not None else low

        # 3. ChoCh detection & Direction Change update
        if prev_close is not None and prev_ph_val is not None and self.ph_val is not None:
            crossover_ph = (prev_close <= prev_ph_val) and (close > self.ph_val)
        else:
            crossover_ph = False

        if prev_close is not None and prev_pl_val is not None and self.pl_val is not None:
            crossunder_pl = (prev_close >= prev_pl_val) and (close < self.pl_val)
        else:
            crossunder_pl = False

        bull_choch = crossover_ph and (self.direction is None or self.direction is False)
        bear_choch = crossunder_pl and (self.direction is True)

        step = (self.atr * self.config.target_step_mult) if self.atr is not None else 0.0

        if bull_choch:
            self.direction = True
            self.trend_entry_price = self.ph_val
            self.current_target = self.trend_entry_price + step
        elif bear_choch:
            self.direction = False
            self.trend_entry_price = self.pl_val
            self.current_target = self.trend_entry_price - step

        direction_change = (self.direction != previous_direction)
        if direction_change and self.direction is not None and self.current_target is not None:
            if self.direction:
                self.t1_level = self.current_target
                self.t2_level = self.current_target + step
                self.t3_level = self.current_target + step * 2.0
            else:
                self.t1_level = self.current_target
                self.t2_level = self.current_target - step
                self.t3_level = self.current_target - step * 2.0

        # 4. Trigger candle pattern definitions (§4)
        body_size = abs(close - open_price)
        prev_body_size = abs(prev_close - prev_open) if prev_close is not None else body_size
        lower_wick = min(open_price, close) - low
        upper_wick = high - max(open_price, close)

        atr_val = self.atr if self.atr is not None else 0.0
        near_ema = abs(close - self.ema55) <= atr_val * self.config.proximity_atr_mult if self.ema55 is not None else False
        near_vwap = abs(close - self.vwap) <= atr_val * self.config.proximity_atr_mult if self.vwap is not None else False
        near_level = near_ema or near_vwap

        pin_bar_bull = lower_wick > body_size * 1.5 and near_level and close > open_price
        pin_bar_bear = upper_wick > body_size * 1.5 and near_level and close < open_price

        if prev_close is not None:
            engulf_bull = (
                close > open_price
                and prev_close < prev_open
                and body_size >= prev_body_size * self.config.engulf_min_body
                and close > prev_open
                and open_price < prev_close
            )
            engulf_bear = (
                close < open_price
                and prev_close > prev_open
                and body_size >= prev_body_size * self.config.engulf_min_body
                and close < prev_open
                and open_price > prev_close
            )
        else:
            engulf_bull = False
            engulf_bear = False

        break_bull = close > prev_high
        break_bear = close < prev_low

        retest_bull = self.ph_val is not None and low <= self.ph_val and close > self.ph_val
        retest_bear = self.pl_val is not None and high >= self.pl_val and close < self.pl_val

        # Gate 5 raw evaluation
        trig_bull_raw = (
            (self.config.trig_pin_bar and pin_bar_bull)
            or (self.config.trig_engulf and engulf_bull)
            or (self.config.trig_break and break_bull)
            or (self.config.trig_retest and retest_bull)
        )
        trig_bear_raw = (
            (self.config.trig_pin_bar and pin_bar_bear)
            or (self.config.trig_engulf and engulf_bear)
            or (self.config.trig_break and break_bear)
            or (self.config.trig_retest and retest_bear)
        )

        gate5_bull = (not self.config.use_gate_trigger) or trig_bull_raw
        gate5_bear = (not self.config.use_gate_trigger) or trig_bear_raw

        # Wick-source fix (§4): priority pin bar > break > retest > engulfing
        other_patterns_bull = (
            (self.config.trig_pin_bar and pin_bar_bull)
            or (self.config.trig_break and break_bull)
            or (self.config.trig_retest and retest_bull)
        )
        engulf_is_the_trigger_bull = (
            self.config.use_gate_trigger
            and self.config.trig_engulf
            and engulf_bull
            and not other_patterns_bull
        )

        other_patterns_bear = (
            (self.config.trig_pin_bar and pin_bar_bear)
            or (self.config.trig_break and break_bear)
            or (self.config.trig_retest and retest_bear)
        )
        engulf_is_the_trigger_bear = (
            self.config.use_gate_trigger
            and self.config.trig_engulf
            and engulf_bear
            and not other_patterns_bear
        )

        self.trig_wick_low_bull = prev_low if engulf_is_the_trigger_bull else low
        self.trig_wick_high_bear = prev_high if engulf_is_the_trigger_bear else high

        # 5. Evaluate Gates 1-4
        gate1_bull = (not self.config.use_gate_choch) or bull_choch
        gate1_bear = (not self.config.use_gate_choch) or bear_choch

        gate2_bull = (not self.config.use_gate_ema) or (self.ema55 is not None and close > self.ema55)
        gate2_bear = (not self.config.use_gate_ema) or (self.ema55 is not None and close < self.ema55)

        gate3_bull = (not self.config.use_gate_adx) or (self.adx is not None and self.adx > self.config.adx_threshold)
        gate3_bear = (not self.config.use_gate_adx) or (self.adx is not None and self.adx > self.config.adx_threshold)

        vwap_slope_len = self.config.vwap_slope_lookback + 1
        if len(self.vwap_history) >= vwap_slope_len:
            vwap_slope_up = self.vwap_history[-1] > self.vwap_history[0]
            vwap_slope_down = self.vwap_history[-1] < self.vwap_history[0]
        else:
            vwap_slope_up = False
            vwap_slope_down = False

        gate4_bull = (not self.config.use_gate_vwap) or (self.vwap is not None and close > self.vwap and vwap_slope_up)
        gate4_bear = (not self.config.use_gate_vwap) or (self.vwap is not None and close < self.vwap and vwap_slope_down)

        # 6. Non-gate filters: Session time filter and daily trade limit
        # Convert UTC event.timestamp to configured session_timezone using zoneinfo
        ts = event.timestamp
        if hasattr(ts, "to_pydatetime"):
            dt_utc = ts.to_pydatetime()
        elif isinstance(ts, str):
            dt_utc = datetime.fromisoformat(ts)
        elif isinstance(ts, datetime):
            dt_utc = ts
        else:
            dt_utc = pd.to_datetime(ts).to_pydatetime()

        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)

        tz = ZoneInfo(self.config.session_timezone)
        local_dt = dt_utc.astimezone(tz)
        local_time = local_dt.time()
        local_date = local_dt.date()

        # Daily trade limit reset tracking
        if self.last_trade_day != local_date:
            self.trades_today = 0
            self.last_trade_day = local_date

        under_daily_limit = self.trades_today < self.config.max_trades_per_day

        # Time window filter
        if not self.config.use_time_filter:
            time_ok = True
        else:
            in_s1 = _is_time_in_session(local_time, self.config.session_1)
            in_s2 = _is_time_in_session(local_time, self.config.session_2)
            in_window = in_s1 or in_s2
            blocked = self.config.block_us_data and _is_time_in_session(local_time, self.config.us_data_session)
            time_ok = in_window and not blocked

        # 7. Entry conditions check (flat-only)
        entry_long = (
            all([gate1_bull, gate2_bull, gate3_bull, gate4_bull, gate5_bull])
            and time_ok
            and under_daily_limit
            and not self.in_position
        )
        entry_short = (
            all([gate1_bear, gate2_bear, gate3_bear, gate4_bear, gate5_bear])
            and time_ok
            and under_daily_limit
            and not self.in_position
        )

        # 8. Stop Loss calculation (§5) — unconditional, computed every bar
        atr_val = self.atr if self.atr is not None else 0.0
        sl_distance_dyn_long = (close - self.trig_wick_low_bull) + atr_val * self.config.dyn_sl_atr_mult
        sl_distance_dyn_short = (self.trig_wick_high_bear - close) + atr_val * self.config.dyn_sl_atr_mult

        if self.config.sl_mode == "Dynamic (ATR/Matrix)":
            sl_price_long = close - sl_distance_dyn_long
            sl_price_short = close + sl_distance_dyn_short
        elif self.config.sl_mode == "Fixed (Points)":
            sl_price_long = close - self.config.fixed_sl_points
            sl_price_short = close + self.config.fixed_sl_points
        elif self.config.sl_mode == "Fixed (%)":
            sl_price_long = close * (1.0 - self.config.fixed_sl_percent / 100.0)
            sl_price_short = close * (1.0 + self.config.fixed_sl_percent / 100.0)
        else:
            sl_price_long = close - sl_distance_dyn_long
            sl_price_short = close + sl_distance_dyn_short

        # 9. Take Profit calculation (§6) — unconditional, computed every bar
        sl_distance_actual_long = close - sl_price_long
        sl_distance_actual_short = sl_price_short - close

        if self.config.tp_mode == "Dynamic (ATR/Matrix)":
            if self.config.dyn_tp_use_matrix:
                tp_price_long = self.t3_level
                tp_price_short = self.t3_level
            else:
                tp_price_long = close + atr_val * self.config.dyn_tp_flat_mult
                tp_price_short = close - atr_val * self.config.dyn_tp_flat_mult
        elif self.config.tp_mode == "Fixed RR":
            tp_price_long = close + sl_distance_actual_long * self.config.rr_ratio
            tp_price_short = close - sl_distance_actual_short * self.config.rr_ratio
        elif self.config.tp_mode == "Fixed (Points)":
            tp_price_long = close + self.config.fixed_tp_points
            tp_price_short = close - self.config.fixed_tp_points
        elif self.config.tp_mode == "Fixed (%)":
            tp_price_long = close * (1.0 + self.config.fixed_tp_percent / 100.0)
            tp_price_short = close * (1.0 - self.config.fixed_tp_percent / 100.0)
        else:
            tp_price_long = close + atr_val * self.config.dyn_tp_flat_mult
            tp_price_short = close - atr_val * self.config.dyn_tp_flat_mult

        # 10. Risk-sizing distance calculation (§7 locked fix)
        # Emitted via SignalEvent.strength as the per-unit SL distance in price terms (to be divided
        # into risk_amount downstream). Strategy has no access to equity.
        if self.config.sl_mode == "Dynamic (ATR/Matrix)":
            sl_distance_actual_long_for_risk = sl_distance_dyn_long
            sl_distance_actual_short_for_risk = sl_distance_dyn_short
        else:
            sl_distance_actual_long_for_risk = abs(close - sl_price_long)
            sl_distance_actual_short_for_risk = abs(sl_price_short - close)

        if self.config.use_risk_sizing and sl_distance_actual_long_for_risk > 0:
            risk_distance_long = sl_distance_actual_long_for_risk
        else:
            risk_distance_long = None

        if self.config.use_risk_sizing and sl_distance_actual_short_for_risk > 0:
            risk_distance_short = sl_distance_actual_short_for_risk
        else:
            risk_distance_short = None

        # 11. Entry signal emission (flat-only)
        symbol = getattr(self.config, "symbol", "XAUUSD")

        if entry_long:
            self.trades_today += 1
            tp_prices_long = (
                [self.t1_level, self.t2_level, self.t3_level]
                if (self.config.tp_mode == "Dynamic (ATR/Matrix)" and self.config.dyn_tp_use_matrix)
                else [tp_price_long]
            )
            # SignalEvent.strength on entry signals carries the per-unit SL distance in price terms
            # (mode-appropriate), to be divided into risk_amount downstream in Portfolio / RiskManager / Engine.
            # It is None if use_risk_sizing is False or if the distance is <= 0.
            return SignalEvent(
                type=EventType.SIGNAL,
                symbol=symbol,
                direction=1,
                strength=risk_distance_long,
                sl_price=sl_price_long,
                tp_prices=tp_prices_long,
                timestamp=event.timestamp,
            )

        if entry_short:
            self.trades_today += 1
            tp_prices_short = (
                [self.t1_level, self.t2_level, self.t3_level]
                if (self.config.tp_mode == "Dynamic (ATR/Matrix)" and self.config.dyn_tp_use_matrix)
                else [tp_price_short]
            )
            # SignalEvent.strength on entry signals carries the per-unit SL distance in price terms
            # (mode-appropriate), to be divided into risk_amount downstream in Portfolio / RiskManager / Engine.
            # It is None if use_risk_sizing is False or if the distance is <= 0.
            return SignalEvent(
                type=EventType.SIGNAL,
                symbol=symbol,
                direction=-1,
                strength=risk_distance_short,
                sl_price=sl_price_short,
                tp_prices=tp_prices_short,
                timestamp=event.timestamp,
            )

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
