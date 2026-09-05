# MS Trend Matrix — Python Port: Requirements & Spec

Source strategy: `strategy-v2-fixed.txt` (5-Gate MS Trend Matrix, wick-fix + engulfing-wiring applied)
Target engine: `backtest_engine_spec.md` (event-driven, modular, XAUUSD)

This document defines exactly what `strategy.py` (and the `config.py` additions it needs)
must reproduce from the Pine Script, bar-by-bar, with no look-ahead. It does not repeat the
engine's own internals (Portfolio/Execution/Risk/Performance) — those already have method
signatures in the engine spec. This is scoped to the strategy layer and the config knobs it needs.

---

## 1. State the strategy must carry between bars

Pine's `var` variables persist across bars — in Python these become instance attributes on
`Strategy`, initialized once in `__init__` and updated in `on_bar`.

| Pine `var` | Python attribute | Purpose |
|---|---|---|
| `phVal`, `plVal` | `self.ph_val`, `self.pl_val` | last confirmed pivot high/low price |
| `phIndx`, `plIndx` | `self.ph_idx`, `self.pl_idx` | bar index of that pivot (for reference only — not needed for signal logic) |
| `direction` | `self.direction` (bool: True=bull, False=bear) | current market-structure bias |
| `atrTS` | `self.atr_trailing_stop` | trailing stop line (visual in Pine; not needed for entries/exits, can skip or keep for parity charting) |
| `entryPrice` | `self.trend_entry_price` | price where current trend leg started (= pivot that triggered ChoCh) |
| `currentTarget` | `self.current_target` | rolling ATR target level (visual ladder; not the same as T1/T2/T3 trade TP) |
| `trendStart` | `self.trend_start_idx` | bar index of trend start (visual only) |
| `t1Level`, `t2Level`, `t3Level` | `self.t1_level`, `self.t2_level`, `self.t3_level` | the ladder used for **Dynamic (ATR/Matrix)** TP — this one *is* trade-relevant |
| `tradesToday`, `lastTradeDay` | `self.trades_today`, `self.last_trade_day` | daily trade cap tracking |
| position-side actives: `activeSL/TP1/TP2/TP3/Entry` | `self.active_sl`, `self.active_tp1/2/3`, `self.active_entry` | levels locked in at entry time, used by exits every bar after |

Indicator rolling state needed (not `var` in Pine, but needs a buffer in Python since we're
streaming bar-by-bar, not vectorized):

- **Pivot high/low** (`ta.pivothigh(msLen, msLen)` / `ta.pivotlow`) — needs a buffer of the last
  `2*msLen + 1` bars' highs/lows to detect a pivot, and the pivot only *confirms* `msLen` bars
  after it occurs (this is a real look-ahead trap — see §4).
- **EMA55** — recursive, needs only last EMA value + `alpha = 2/(len+1)`.
- **ATR** — Wilder's smoothing, needs only last ATR value + true range of current bar.
- **VWAP** — needs running `sum(src*vol)`, `sum(vol)`, `sum(vol*src^2)`, reset on session/anchor change.
- **VWAP slope lookback** — needs a buffer of the last `vwapSlopeLookback + 1` VWAP values.
- **ADX/DMI** — Wilder's smoothing on +DM/-DM/TR, needs last smoothed values.
- **Engulfing prior-bar wick** — needs `open[1], close[1], high[1], low[1]` → buffer of last 2 bars minimum.
- **Break-of-prior-candle** — needs `high[1], low[1]` → same 2-bar buffer.

**Recommendation:** give `Strategy` a small internal `deque(maxlen=N)` of raw OHLC bars
(N = `2*msLen + 2` is enough to cover pivot detection + 1-bar-back lookups) rather than
tracking every indicator's own bespoke buffer. Indicators that need more history than that
(VWAP slope lookback, if configured larger than the pivot window) get their own small deque.

---

## 2. Indicator porting notes specific to this strategy

The engine spec's Pine→Python table covers the generic cases. Two used here need their own note
because they're easy to get subtly wrong:

**`ta.pivothigh(msLen, msLen)` / `ta.pivotlow`**
Not a simple rolling max/min. A pivot high at bar `i` requires bar `i`'s high to be strictly the
highest in the window `[i-msLen, i+msLen]`. Because it needs `msLen` bars *after* `i` to confirm,
**the pivot value becomes known only at bar `i + msLen`**, not at bar `i` itself. Pine handles
this invisibly via its lookahead-safe historical referencing (`high[msLen]` at confirmation time
refers back to the actual pivot bar). In the Python port:
- Maintain a buffer of the last `2*msLen + 1` bars.
- At each new bar, check whether the bar `msLen` positions back in that buffer is a pivot
  (strictly highest/lowest in the full window now available).
- If yes, that's when `phVal`/`plVal` update — `msLen` bars later than the pivot bar itself,
  exactly matching Pine's `high[msLen]` reference. **This is not a bug to fix — it's how the
  original strategy behaves, and must be reproduced exactly for parity.**

**`ta.dmi(diLength, adxSmoothing)`**
Returns `[plusDI, minusDI, adx]`. This is two-stage Wilder smoothing: first on +DM/-DM/TR to get
DI+/DI-, then a second Wilder smoothing on the DX series to get ADX. Get this from a reference
implementation rather than re-deriving — it's a common source of off-by-one/warm-up bugs.

**`time(timeframe.period, session, timezone)` (session windows)**
Pine's session string (`"1300-1500"`) + timezone (`"Asia/Bangkok"`) needs a Python equivalent:
convert each bar's UTC timestamp to the configured timezone (via `zoneinfo`/`pytz`), extract
`HH:MM`, and check whether it falls in `[start, end)`. Handle overnight sessions (e.g. `"2300-0100"`)
if any of Tii's session windows cross midnight — currently none do (`1300-1500`, `2000-2300`,
`1915-1945` are all same-day), so a same-day-only implementation is sufficient for v1, but flag
this assumption in the code comment.

---

## 3. Gate-by-gate logic (must match 1:1)

All five gates plus the two non-gate filters (session time, daily limit) are AND-ed together per
direction. Each gate is `not use_gate_X or <condition>` — i.e. **disabled gates always pass**.

| Gate | Bull condition | Bear condition | Config knobs |
|---|---|---|---|
| 1. ChoCh | `bull_choch` fired this bar | `bear_choch` fired this bar | `use_gate_choch` |
| 2. EMA55 side | `close > ema55` | `close < ema55` | `use_gate_ema` |
| 3. ADX | `adx > adx_threshold` (same value both directions) | same | `use_gate_adx`, `adx_threshold` |
| 4. VWAP | `close > vwap and vwap_slope_up` | `close < vwap and vwap_slope_down` | `use_gate_vwap`, `vwap_slope_lookback` |
| 5. Trigger candle | one of pin bar / engulfing / break / retest fired, per §5 below | mirrored | `use_gate_trigger`, `trig_pin_bar`, `trig_engulf`, `trig_break`, `trig_retest` |

`bull_choch = crossover(close, ph_val) and direction == bear_or_neutral`
`bear_choch = crossunder(close, pl_val) and direction == bull`

`crossover(a, b)`: `a[1] <= b[1] and a > b`. `crossunder`: `a[1] >= b[1] and a < b`. Standard
port per the engine spec's table — note `b` here is `ph_val`/`pl_val`, which only changes value
on pivot-confirmation bars (see §2), so it's flat between confirmations, not a live series.

Non-gate filters (still required for entry, not toggleable per-gate but each has its own on/off):
- `time_ok = in_trading_window and not blocked_by_us_data` (see §2 session note)
- `under_daily_limit = trades_today < max_trades_per_day`

**Entry condition:** `all 5 gates (bull) and time_ok and under_daily_limit and flat` → long signal.
Mirror for short. **Flat-only** — no reversals, no pyramiding, no adding to a position. This means
`Portfolio.on_signal` in the engine can assume every non-zero signal from this strategy only ever
arrives when position size is already zero (the strategy enforces that itself), but the engine's
existing "close opposite position first" logic should stay in place as a safety net — cheap
insurance, not required for this strategy to behave correctly.

---

## 4. Trigger candle (Gate 5) — pattern definitions

Only evaluated when `use_gate_trigger` is True; each of the four patterns has its own toggle
and they're OR-ed together.

- **Pin bar bull:** `lower_wick > body_size * 1.5 and near_level and close > open`
- **Pin bar bear:** `upper_wick > body_size * 1.5 and near_level and close < open`
  where `near_level = near_ema or near_vwap`, `near_ema = abs(close - ema55) <= atr * proximity_atr_mult`, `near_vwap = abs(close - vwap) <= atr * proximity_atr_mult`
- **Engulfing bull:** `close > open and close[1] < open[1] and body_size >= prev_body_size * engulf_min_body and close > open[1] and open < close[1]`
- **Engulfing bear:** mirrored
- **Break bull:** `close > high[1]` — **Break bear:** `close < low[1]`
- **Retest bull:** `not na(ph_val) and low <= ph_val and close > ph_val` — **Retest bear:** mirrored on `pl_val`

**SL wick-source fix (applied in strategy-v2-fixed.txt, must be preserved in the port):**
`trig_wick_low_bull` = `low[1]` if engulfing is the *only* pattern that fired this bar (priority:
pin bar > break > retest > engulfing), else `low` (current bar). Mirror for
`trig_wick_high_bear` / `high[1]` / `high`. When `use_gate_trigger` is False, always use the
current bar (`low`/`high`) — unchanged from pre-fix behavior. This logic must be ported exactly
as-is; it's not a simplification target.

---

## 5. Stop Loss — 3 modes (`sl_mode`)

```
sl_distance_dyn_long  = (close - trig_wick_low_bull)  + atr * dyn_sl_atr_mult
sl_distance_dyn_short = (trig_wick_high_bear - close) + atr * dyn_sl_atr_mult

sl_price_long:
  "Dynamic (ATR/Matrix)" → close - sl_distance_dyn_long
  "Fixed (Points)"       → close - fixed_sl_points
  "Fixed (%)"            → close * (1 - fixed_sl_percent / 100)

sl_price_short: mirrored with +
```

Note `trig_wick_low_bull`/`trig_wick_high_bear` are computed on **every** bar regardless of
`sl_mode` (cheap, and Fixed RR's risk leg needs to be consistent) — port that as unconditional
too, don't gate the computation itself.

---

## 6. Take Profit — 4 modes (`tp_mode`)

```
slDistance_long_actual  = close - sl_price_long     # actual distance from whichever SL mode fired
slDistance_short_actual = sl_price_short - close

tp_price_long:
  "Dynamic (ATR/Matrix)" → t3_level if dyn_tp_use_matrix else close + atr * dyn_tp_flat_mult
  "Fixed RR"             → close + slDistance_long_actual * rr_ratio
  "Fixed (Points)"       → close + fixed_tp_points
  "Fixed (%)"            → close * (1 + fixed_tp_percent / 100)

tp_price_short: mirrored with -
```

**Ladder targets** (`t1_level`, `t2_level`, `t3_level`) are set **only when `direction_change`
happens** (i.e. on the ChoCh bar that flips trend), from `current_target` and `atr * target_step_mult`:
```
t1_level = current_target
t2_level = current_target + atr * target_step_mult   (bull; - for bear)
t3_level = current_target + atr * target_step_mult*2 (bull; - for bear)
```
These persist unchanged for the rest of that trend leg — they are NOT recalculated every bar,
only at the direction-change bar. This is trend-leg state, not per-trade state; carry it as
`self.t1_level` etc., separate from the per-position `active_tp1/2/3` which get assigned once at
entry and then frozen for that trade's lifetime.

---

## 7. Position sizing from risk % — DECIDED: fix the gating quirk

**Decision (locked):** fix it. The Python port gates on the mode-appropriate distance, not the
always-dynamic one. This is a deliberate improvement over the Pine source, not a parity break —
documented here so it's not mistaken for a porting slip later.

```
risk_amount = equity * (risk_per_trade_pct / 100)

sl_distance_actual_long  = sl_distance_dyn_long  if sl_mode == "Dynamic (ATR/Matrix)" else abs(close - sl_price_long)
sl_distance_actual_short = sl_distance_dyn_short if sl_mode == "Dynamic (ATR/Matrix)" else abs(sl_price_short - close)

qty_from_risk_long = (
    risk_amount / sl_distance_actual_long
    if use_risk_sizing and sl_distance_actual_long > 0 else None
)
qty_from_risk_short = (
    risk_amount / sl_distance_actual_short
    if use_risk_sizing and sl_distance_actual_short > 0 else None
)
```

The only change from the Pine source: the `> 0` gate now checks the **same distance that's about
to be used in the division**, whichever `sl_mode` produced it — instead of always checking the
dynamic distance regardless of mode. Division result is identical to Pine's in the vast majority
of bars (the quirk rarely changed the outcome); this just makes the gate consistent with what it's
gating.

The engine's own `RiskManager.position_size()` (per the engine spec) is a simpler generic
`risk_amount / (stop_distance * 100)` and doesn't know about `sl_mode` branching — **this
strategy-specific sizing lives in `Strategy` itself** (as shown above) and is passed downstream
via the extended `SignalEvent` (see §9 — same extension used for ladder quantities carries the
sizing too). `RiskManager` still owns `max_position_lots` capping and the drawdown halt check;
`Strategy`'s risk-% calc feeds it a proposed quantity, `RiskManager` can still clip it to
`max_position_lots` as a hard ceiling.

---

## 8. Order of operations within `on_bar` (must match Pine's execution order)

Pine evaluates top-to-bottom in source order every bar. To reproduce identical entry/exit timing:

1. Update all indicators (pivot buffer, EMA, ATR, VWAP, ADX) with the new bar.
2. Update `direction` / ChoCh detection; if ChoCh fires, update `t1/t2/t3_level` and trend state.
3. Evaluate all 5 gates + time filter + daily limit → `long_condition` / `short_condition`.
4. Compute SL/TP prices for both directions (cheap, always computed).
5. Compute risk-based qty for both directions.
6. **If `long_condition` or `short_condition` and currently flat:** emit entry signal, lock in
   `active_sl`, `active_tp1/2/3`, `active_entry`, increment `trades_today`.
7. **Early exit check** (independent of the above): if in a position and price has reclaimed
   both EMA55 and VWAP against it, emit close signal — **this skips the ladder/single exit
   check below for that bar** (Pine's `if earlyExitLong ... ; if inLong and not earlyExitLong ...`).
8. **Ladder or single exit:** if in a position, not early-exited this bar, emit the
   TP/SL exit structure appropriate to `use_ladder = tp_mode == "Dynamic (ATR/Matrix)" and dyn_tp_use_matrix`.

Step 7/8 ordering matters: early exit takes priority and suppresses the TP/SL exit logic on the
same bar, exactly as the Pine `if...if` sequence does (not an `elif`, but the second block's own
condition includes `not earlyExitLong` so it's functionally exclusive).

---

## 9. Ladder exits — DECIDED: Option A (partial-close signal extension)

The Pine version stacks three `strategy.exit()` calls (T1/T2/T3), each a bracket order (limit +
shared stop) on a **percentage of the original position**. The v1 engine spec's `SignalEvent` and
single-position `Portfolio`/`Execution` have no native partial-close concept, so this requires
extending three files beyond what `backtest_engine_spec.md` originally sketched:

**`events.py` — `SignalEvent` gets two new fields:**
```python
@dataclass
class SignalEvent:
    type: EventType = EventType.SIGNAL
    symbol: str = "XAUUSD"
    direction: int = 0             # 1=long, -1=short, 0=flat/close
    strength: float = 1.0          # sizing hint (risk-based qty, see §7) — used on ENTRY signals only
    close_fraction: Optional[float] = None   # 0.0–1.0 of ORIGINAL entry quantity to close (T1/T2); None = full close of whatever remains (T3/SL/EARLY)
    exit_tag: Optional[str] = None           # "T1" | "T2" | "T3" | "SL" | "EARLY" — for trade_log labeling
    sl_price: Optional[float] = None         # attached on entry signals so Portfolio can store it on the Position
    tp_prices: Optional[list] = None         # [tp1, tp2, tp3] attached on entry signals, ladder-aware
    timestamp: object = None
```

**`portfolio.py` — `Position` and `on_signal`/`on_fill` need partial-quantity awareness:**
- `Position` gains `remaining_quantity` (starts equal to `quantity`, decrements as ladder legs fill)
  and `sl_price`/`tp_prices` (populated from the entry `SignalEvent`, not recomputed later).
- `on_signal`: a signal with `close_fraction` set and `direction == 0` means "close this fraction
  of the existing position", not "close everything" — compute `qty_to_close = position.remaining_quantity * close_fraction` (fall back to the *original* full quantity's fraction if that's closer to how you want T1/T2 sized — see note below) and emit an `OrderEvent` for that quantity only.
- `on_fill`: when a partial close fills, reduce `remaining_quantity`, compute realized PnL on
  **only the closed portion**, append a trade_log row for that leg (tagged via `exit_tag`), and
  keep the position open with the reduced quantity. Only append the position-closing trade_log
  row (and delete from `self.positions`) when `remaining_quantity` reaches ~0.

  **Sizing note to settle during implementation:** Pine's `qty_percent = t1Percent` /
  `qty_percent = t2Percent` are both percentages **of the ORIGINAL position size**, not of
  whatever remains after T1 already closed (confirm this reading against Pine's
  `strategy.exit(qty_percent=...)` docs when coding — this is the standard interpretation but
  worth a real check since getting the base wrong changes every downstream fill size). If
  original-size-based, `Position` should keep `original_quantity` alongside `remaining_quantity`
  so T2's fraction is computed off the right base.

**`execution.py` — `on_order` needs no interface change**, just needs to accept the smaller
partial-close quantities that `Portfolio` now emits like any other order — no new logic required
there beyond what the engine spec already defines, since a partial-close order is just a normal
close-direction market order for a smaller size.

**`strategy.py` — `on_bar` checks ladder crossings every bar while in a position:**
Per bar, using **current bar's high/low** (not close — TP/SL levels can be crossed intrabar even
under the engine's close±spread fill assumption; check against high/low to decide *whether* a
level was hit, then fill at the engine's existing close±spread convention, consistent with how
the non-ladder single-exit path already must work):
- If `high >= active_tp1` (long) or `low <= active_tp1` (short) and T1 not yet taken → emit
  `SignalEvent(direction=0, close_fraction=t1_percent/100, exit_tag="T1")`.
- Same pattern for T2 → `active_tp2`, T3 (closes remainder, no fraction needed — full close) →
  `active_tp3`.
- If `low <= active_sl` (long) or `high >= active_sl` (short) → close **whatever quantity
  remains** at SL, tagged `"SL"` — this fires regardless of which T-legs already closed, matching
  Pine's shared-stop-across-all-three-exit-calls behavior.
- Early exit (§8 step 7) still takes priority and, if it fires, closes the full remaining
  quantity tagged `"EARLY"`, skipping the ladder check for that bar entirely — same ordering as
  before.
- If multiple levels are crossed within the same bar (e.g. both TP1 and SL touched on a big-range
  bar), pick one deterministic priority order and document it in code — recommend SL-priority
  (assume the adverse move happened first) as the conservative default, but this is a modeling
  choice worth a one-line comment since Pine's own intrabar broker emulator has its own tie-break
  behavior that a close-only engine can't fully replicate (accepted divergence, same category as
  §11's no-intrabar-simulation note).

**`performance.py` / `reporter.py`:** trade_log rows are now per-leg (T1/T2/T3/SL/EARLY) rather
than one row per position. `Performance.compute()`'s trade-count-based metrics (`win_rate`,
`profit_factor`, `avg_trade_pnl`) should decide once, and document, whether "a trade" means a leg
or a whole position — recommend **whole-position** for win-rate/profit-factor (aggregate the legs'
PnL back to one number per position before computing those stats) since that's what "win rate"
intuitively means to you as a trader, while `export_trades_csv` can still show the individual
T1/T2/T3/SL legs for transparency. Flag this as a small decision to confirm when `performance.py`
is actually written, not blocking for now.

---

## 10. Config additions needed (`config.py`)

The engine spec's `BacktestConfig` only sketches a placeholder comment for strategy params. This
strategy needs all of the following added as fields, grouped to mirror the Pine `input.*` groups:

```python
# Data
csv_path: str = "data/xauusd_m5.csv"
symbol: str = "XAUUSD"
timeframe: str = "5m"                 # bars_per_year = 288 * 252 for M5, per engine spec table

# Market Structure
ms_len: int = 7
atr_length: int = 14
atr_mult: float = 4.0
target_step_mult: float = 2.0

# EMA
ema_len: int = 55

# VWAP
vwap_anchor: str = "Session"          # Session | Week | Month | Quarter | Year
vwap_slope_lookback: int = 3

# ADX
adx_smoothing: int = 14
di_length: int = 14
adx_threshold: int = 20

# Gates
use_gate_choch: bool = True
use_gate_ema: bool = True
use_gate_adx: bool = False
use_gate_vwap: bool = True
use_gate_trigger: bool = False

# Trigger candle
trig_pin_bar: bool = True
trig_engulf: bool = True
trig_break: bool = True
trig_retest: bool = True
proximity_atr_mult: float = 0.5
engulf_min_body: float = 1.0

# Session time (ICT)
use_time_filter: bool = True
session_1: str = "1300-1500"
session_2: str = "2000-2300"
block_us_data: bool = True
us_data_session: str = "1915-1945"
session_timezone: str = "Asia/Bangkok"

# TP / SL
sl_mode: str = "Dynamic (ATR/Matrix)"     # Dynamic (ATR/Matrix) | Fixed (Points) | Fixed (%)
dyn_sl_atr_mult: float = 0.0
fixed_sl_points: float = 500
fixed_sl_percent: float = 0.3
tp_mode: str = "Dynamic (ATR/Matrix)"     # Dynamic (ATR/Matrix) | Fixed RR | Fixed (Points) | Fixed (%)
dyn_tp_use_matrix: bool = True
dyn_tp_flat_mult: float = 2.0
t1_percent: float = 60
t2_percent: float = 20
rr_ratio: float = 2.0
fixed_tp_points: float = 1000
fixed_tp_percent: float = 0.6
use_early_exit: bool = True

# Risk
risk_per_trade_pct: float = 1.0
use_risk_sizing: bool = True
max_trades_per_day: int = 3
```

All defaults above are copied directly from the Pine `input.*` defaults for 1:1 parity out of
the box.

---

## 11. Config must behave like TradingView's Inputs panel — DECIDED, locked requirement

You should be able to change any strategy setting between runs the same way you'd flip a
checkbox or retype a number in TradingView's Inputs panel — **without editing engine code**.
`BacktestConfig` (§10) is that panel; `run.py` is where you set the values, exactly like
TradingView's Inputs tab is where you'd change them there. Concretely, this means:

- Every single Pine `input.*` in the strategy has exactly one matching `BacktestConfig` field
  (§10's list) — nothing strategy-specific is hardcoded inside `strategy.py`. If it was an input
  in Pine, it's a config field in Python, no exceptions.
- Changing a setting is a one-line edit in `run.py`:
  ```python
  config = BacktestConfig(sl_mode="Fixed (Points)", fixed_sl_points=800, use_gate_adx=True)
  engine = BacktestEngine(config)
  engine.run()
  ```
  No touching `strategy.py`, `portfolio.py`, or any other engine file to try a different SL mode,
  toggle a gate, or change a lookback length — same mental model as toggling a checkbox on
  TradingView and clicking "Run backtest" again.
- The parameter-sweep pattern already sketched in the engine spec (`itertools.product` over
  config fields) works out of the box for **any** field in `BacktestConfig`, not just the
  SMA-fast/slow example shown there — since every knob is just a dataclass field, sweeping ADX
  threshold, risk %, or gate combinations is the same `for` loop pattern, just over different
  field names.
- `Strategy.__init__` takes the full `BacktestConfig` (or a strategy-relevant subset of it) as a
  constructor argument and reads every threshold/toggle from there — never a bare literal like
  `if adx_val > 20:` inside the strategy logic. This is what makes re-running with different
  settings free (no rebuild) instead of requiring code edits per experiment.

This requirement doesn't add new files or events — it's a discipline constraint on how
`config.py` and `strategy.py` are written, worth stating explicitly so it isn't lost during
implementation.

---

## 12. Explicit out-of-scope for this port (per engine spec's v1 constraints)

- No plotting/visual objects (lines, labels, tables, gate-debug labels) — those are Pine chart
  artifacts with no backtest-correctness role. Skip entirely.
- No intrabar path simulation — SL/TP checked against bar close ± spread per the engine spec,
  same as any other strategy on this engine. This means the strategy's SL/TP levels are
  **computed** exactly as Pine does, but **fill timing** follows the engine's existing
  fill-at-close-±-spread rule, not Pine's own intrabar broker emulator. This is a known,
  accepted divergence from live/TradingView behavior per the engine spec's stated v1 scope —
  flagging here so it's not mistaken for a porting error later.
- Reversal/pyramiding: not applicable — strategy is flat-only by construction (§3).
- **OANDA auto-download integration — deferred, not forgotten.** Tii has a standalone
  `oanda_download.py` script (fetches XAUUSD M5 candles via OANDA's REST API, pages past the
  5000-candle-per-request limit, writes the same `time,open,high,low,close,volume` CSV shape
  `DataHandler` expects). The idea of having the engine auto-fetch missing data instead of
  requiring a manual pre-run of that script is a good one, but is explicitly **out of scope for
  `data_handler.py` itself** — see decision #7 below for the reasoning and the planned approach.

---

## 13. Decisions log

| # | Decision | Status |
|---|---|---|
| 1 | §7 risk-sizing gate quirk | **Fixed** — gates on mode-appropriate distance, not always-dynamic |
| 2 | §9 ladder exits | **Option A** — `SignalEvent` extended with `close_fraction`/`exit_tag`, `Portfolio`/`Position` track `remaining_quantity` |
| 3 | §11 config-driven settings | **Locked requirement** — every Pine input maps 1:1 to a `BacktestConfig` field, zero hardcoded literals in `strategy.py` |
| 4 | Timeframe | **M5** — `bars_per_year = 288 × 252` |
| 5 | Data source | **OANDA OHLC CSV**, tick-count volume (expected/correct for VWAP, same as live) |
| 6 | T1/T2 ladder % base | **% of original position size**, confirmed by example (0.1 lot → 60% T1 = 0.06 lot) |
| 7 | OANDA auto-download | **Deferred** — kept as its own module/prompt AFTER `data_handler.py` is QA-approved, not folded into it. Reasons: (a) both specs lock deps to pandas/numpy/matplotlib only — `requests` is a new dependency needing an explicit decision, not a silent addition; (b) API-token/secret handling doesn't belong in a deterministic, pure CSV-loader component; (c) network fetch inside a backtest run risks non-reproducible runs (today's fetch ≠ yesterday's if new bars have accumulated). Planned shape: a separate small module (e.g. `backtest/data_fetch.py`) or keep `oanda_download.py` standalone, with `run.py` optionally invoking it as an explicit pre-step when the configured `csv_path` doesn't exist — never automatically mid-run. |

## 14. Answered — locked

1. **Timeframe: M5.** `bars_per_year` for Performance metrics = `288 × 252` (per the engine
   spec's own M5 row). `Strategy`'s session/VWAP-anchor logic must be sane at M5 resolution —
   in particular the pivot confirmation lag (§2, `msLen` bars each side) and `vwap_slope_lookback`
   are both in **bar units**, so at M5 the defaults (`ms_len=7` → ChoCh confirms 35 minutes after
   the actual pivot bar; `vwap_slope_lookback=3` → a 15-minute slope window) are worth keeping in
   mind when interpreting early results — not a code change, just context for reading output.
2. **Data source: OANDA OHLC CSV.** No separate volume feed — OANDA's `volume` field (if present
   in the export) is tick-count, not real traded volume, which matters for two places in the
   strategy:
   - **VWAP** uses `volume` directly in its `sum(src*vol)` weighting (§2). Tick-count-as-volume is
     the standard workaround for FX/CFD instruments (XAUUSD has no centralized volume) and is
     almost certainly what the original Pine strategy is already doing on TradingView's own OANDA
     feed — so this is **consistent with the live strategy**, not a new divergence. No fix needed,
     just documenting why `volume` in the CSV is tick count and that this is expected/correct.
   - **`DataHandler`** (engine spec §2) must handle `volume` being present-but-not-real-traded-volume,
     and must still handle missing/zero volume gracefully per the engine spec's existing rule, since
     some OANDA exports omit it or report 0 on thin bars.
   - **Weekend/session gaps:** OANDA CSVs typically have no bars during the weekend market closure.
     `VWAP`'s `Session` anchor (`timeframe.change("D")`) must reset correctly across that gap — i.e.
     detecting a new calendar day should still work fine since pandas `Timestamp.date()` comparison
     doesn't care about the size of the gap, just that the date changed. No special weekend-gap
     handling needed in `DataHandler` itself beyond what §2 (no look-ahead, sorted-by-time) already
     specifies — flagging only so it's tested against a real OANDA export, not assumed.
3. **T1/T2 = % of ORIGINAL position size, confirmed with your example** (60% of a 0.1 lot entry =
   0.06 lot closed at T1, regardless of what's left when T1 fires). This confirms the §9 sizing
   note: `Position.original_quantity` is the base for both `t1_percent` and `t2_percent`
   fractions — **not** `remaining_quantity`. Concretely:
   ```
   qty_at_t1 = position.original_quantity * (t1_percent / 100)   # e.g. 0.1 * 0.60 = 0.06
   qty_at_t2 = position.original_quantity * (t2_percent / 100)   # e.g. 0.1 * 0.20 = 0.02
   qty_at_t3 = position.remaining_quantity                        # whatever's left, e.g. 0.02
   ```
   `remaining_quantity` still exists and still decrements after each leg (needed for T3's
   "close whatever's left" and for the SL-hits-mid-ladder case), but the T1/T2 **fraction
   calculations themselves** always reference `original_quantity`, never `remaining_quantity`.
   This is now locked into `Position`'s two-field design from §9 — no further change needed there,
   this just confirms which field feeds the T1/T2 math.

---

## 15. QA Test Checklist — pass/fail criteria per module

Purpose: when Gemini hands back a module, this is what gets checked against — not general code
review, but conformance to the logic locked in §1–§14. Each item is either a concrete
input→expected-output case (hand-computable, so a mismatch is unambiguous) or a specific
behavioral property to trace through the code. "Pass" means the code's actual output/behavior
matches the stated expectation; anything else is a defect to send back to Gemini.

### 15.1 `events.py`

- [ ] `SignalEvent` has all six fields from §9: `direction`, `strength`, `close_fraction`,
      `exit_tag`, `sl_price`, `tp_prices` — plus base `symbol`/`timestamp`. No fields silently
      dropped from the engine spec's original design (`type` still present, still defaults per
      `EventType.SIGNAL`).
- [ ] `close_fraction` defaults to `None`, not `0.0` or `1.0` — a full close must be
      distinguishable from "close_fraction not specified" if any code path checks `is None`.
- [ ] `tp_prices` is typed as `Optional[list]` and nothing enforces exactly 3 elements at the
      dataclass level — a single-TP mode (Fixed RR / Fixed Points / Fixed %) legitimately passes
      a 1-element (or None) list; only Dynamic+Matrix ladder mode populates all 3.

### 15.2 `data_handler.py`

- [ ] Given a CSV with a Fri-23:55 → Mon-00:00 gap (weekend), `has_more()`/`get_bar()` step
      through the gap with no special-cased logic, no crash, no inserted synthetic bars.
- [ ] A row with empty/NaN `volume` does not raise — either coerced to `0.0` or forward-filled,
      but must not propagate `NaN` into VWAP's running sums downstream (NaN would silently poison
      every subsequent VWAP value for the rest of the session).
- [ ] `current_time()` returns the timestamp of the **most recently delivered** bar, not the next
      undelivered one — off-by-one here breaks every timestamp-dependent log/report downstream.
- [ ] Confirm no column is read from row `i+1` or later while serving row `i` — `get_bar()` should
      be traceable as touching only `self.df.iloc[self.current_index]` (or equivalent) before
      incrementing.

### 15.3 `strategy.py` — pivot / ChoCh (§2, §3)

**Hand-computable case** — `ms_len = 2` (small for tractability), bar highs (0-indexed):
`[10, 11, 15, 12, 11, 9, 8, 7, 9, 13]`. Bar 2 (high=15) is a pivot high because it's strictly the
highest in the window `[0, 4]` (indices 0-4, `msLen=2` each side). Expected: `phVal` becomes
known (updates to 15) **only when processing bar 4**, not bar 2 — because confirmation requires
2 bars *after* the pivot bar to exist. Trace the code with this sequence and confirm `self.ph_val`
is still unset through bars 0-3 and becomes `15` exactly at bar 4. This is the single most
important check in the whole module — an off-by-`msLen` error here silently creates look-ahead
bias that won't show up as a crash, only as inflated backtest performance.

- [ ] Pivot confirmation lag matches the hand case above exactly (updates at bar `pivot_idx + ms_len`, not `pivot_idx`).
- [ ] `bull_choch` only evaluates when `direction` is bear/neutral (doesn't fire on an already-bullish trend) — feed two consecutive bull crossovers and confirm only the first fires `bull_choch`.
- [ ] `crossover`/`crossunder` need a **previous-bar** value of both series — confirm the strategy buffers at minimum the prior bar's `close` and prior bar's `ph_val`/`pl_val`, not just current values.

### 15.4 `strategy.py` — gates (§3, §4)

- [ ] All 5 gates individually toggle to "always pass" when their `use_gate_*` flag is False —
      test by setting each flag False one at a time with a bar that would otherwise fail that
      gate, confirm entry still fires (assuming all other gates pass).
- [ ] Gate 5 pattern priority (§4): construct a bar where **both** a break condition and an
      engulfing condition are true simultaneously. Confirm `trig_wick_low_bull`/`trig_wick_high_bear`
      uses the **current bar** wick (break wins priority), not `[1]` — i.e. engulfing only
      controls the wick reference when it's the *sole* fired pattern, per §4's exact wording.
- [ ] Session time filter: feed a bar timestamped so that, after conversion from UTC to
      `Asia/Bangkok`, it falls inside `"1300-1500"` local — confirm `time_ok = True`. Feed a bar
      1 minute after `1500` local — confirm `time_ok = False`. This checks the UTC→ICT conversion
      is actually applied, not skipped.
- [ ] `under_daily_limit` resets at a new calendar day (in the strategy's configured timezone, not
      UTC — confirm which one §2 assumed and that the code matches) and blocks a 4th same-day
      trade when `max_trades_per_day = 3`.

### 15.5 `strategy.py` — SL/TP calculation (§5, §6)

**Hand-computable case** — `close = 2000.0`, `atr = 5.0`, `dyn_sl_atr_mult = 0.5`,
current-bar `low = 1997.0` (long trade, no engulfing trigger, so wick = current bar):
`sl_distance_dyn_long = (2000.0 - 1997.0) + 5.0*0.5 = 3.0 + 2.5 = 5.5` → `sl_price_long = 1994.5`.
Confirm exact match, not approximate.

- [ ] SL mode switch: same inputs above, but `sl_mode = "Fixed (Points)"`, `fixed_sl_points = 500`
      → confirm `sl_price_long = 2000.0 - 500 = 1500.0` (flat subtraction, no unit conversion —
      §5 has no XAUUSD-point-to-price scaling, confirm the code doesn't invent one).
- [ ] Fixed RR TP reads its risk leg from `slDistance_long_actual = close - sl_price_long`
      (the *actual* SL price already chosen by `sl_mode`, not always the dynamic one) — switch
      `sl_mode` between all 3 modes with `tp_mode = "Fixed RR"` fixed, confirm the TP distance
      changes accordingly each time (same "read the actual mode's distance" principle as the §7
      risk-sizing fix, applied here to TP).
- [ ] `t1_level`/`t2_level`/`t3_level` update **only on `direction_change`** bars — feed several
      bars within the same trend (no ChoCh) and confirm these three values stay frozen, not
      recomputed every bar.

### 15.6 `strategy.py` — risk-sizing fix (§7, locked decision)

- [ ] With `sl_mode = "Fixed (Points)"`, construct a bar where the (unused, always-computed)
      dynamic distance would be `≤ 0` — confirm `qty_from_risk_long` is **still computed** (not
      wrongly gated to `None`), since the fix means the gate now checks the Fixed-mode distance,
      which can be positive even when the incidental dynamic distance isn't. If this still returns
      `None` in that case, the fix wasn't actually applied, just relocated.
- [ ] Division uses the same distance variable that passed the gate check (no mismatch between
      what was tested and what was divided by).

### 15.7 `portfolio.py` — ladder / partial closes (§9, locked decision)

**Hand-computable case** — entry `0.1` lot, `t1_percent = 60`, `t2_percent = 20`:
- T1 fires → expect `qty_at_t1 = 0.1 * 0.60 = 0.06` closed, `remaining_quantity = 0.04`,
  `original_quantity` still `0.1`.
- T2 fires later (same position) → expect `qty_at_t2 = 0.1 * 0.20 = 0.02` closed (**from
  `original_quantity`, not from the `0.04` remaining** — the exact case §14 confirmed),
  `remaining_quantity = 0.02`.
- T3 fires → expect the full remaining `0.02` closes, position removed from `self.positions`.
- [ ] Confirm all three numbers above match exactly when traced through the code — this is the
      single highest-value check in `portfolio.py` since a wrong base (remaining vs. original)
      silently under- or over-closes every ladder trade without erroring.
- [ ] SL-mid-ladder case: T1 fires (0.06 closed, 0.04 remaining), then SL is hit before T2/T3 —
      confirm exactly `0.04` closes at SL (the remaining amount), not `0.1` (original) or `0.02`
      (T2's fraction).
- [ ] Same-bar multi-level touch: construct a bar whose high/low range crosses both a TP level and
      the SL level in the same bar. Confirm the code applies the documented SL-priority tie-break
      from §9 (SL wins), and that this rule is actually implemented, not just commented as intent.
- [ ] Trade log gets one row per closed **leg** (T1/T2/T3/SL/EARLY each produce their own row per
      §9), not one aggregated row per position — needed for `export_trades_csv` per the engine
      spec, and for the win-rate aggregation decision flagged in §9.

### 15.8 `strategy.py` — execution order (§8)

- [ ] Early exit and ladder/single-exit are mutually exclusive **on the same bar** — construct a
      bar where both the early-exit condition (EMA55+VWAP reclaim) and a TP level are true
      simultaneously; confirm only the early-exit signal fires (tagged `"EARLY"`), and the
      TP/ladder check is skipped entirely for that bar, not just deprioritized.
- [ ] Entry evaluation happens before exit evaluation within the same `on_bar` call, matching
      Pine's top-to-bottom source order — a bar that both closes an existing position (via SL/TP)
      and would independently qualify as a fresh entry should NOT enter on the same bar the old
      position closes (Pine's `strategy.position_size == 0` check reads position state *before*
      that bar's exits are processed) — confirm the port preserves this; getting it backwards
      silently creates trades the original strategy never took.

### 15.9 `config.py`

- [ ] Every field listed in §10 (as amended by §14's `csv_path`/`timeframe` addition) is present
      with the exact default value shown, no field renamed or dropped.
- [ ] No literal threshold/toggle value appears hardcoded anywhere in `strategy.py` — grep for
      bare numbers next to comparison operators (`> 20`, `* 1.5`, `== "Session"` etc.) that aren't
      reading from `self.config.<field>` — per §11's locked requirement. Any hit is a defect.
- [ ] A `BacktestConfig(...)` constructed with a non-default value for any single field (e.g.
      `use_gate_adx=True`) actually changes `Strategy`'s behavior without touching any other file
      — spot-check 2-3 fields, not all ~35, to confirm the wiring pattern holds throughout rather
      than only for a few fields someone remembered to wire up.

### 15.10 Whole-engine acceptance (per original engine spec, re-stated for this strategy)

- [ ] Runs end-to-end on a real OANDA M5 XAUUSD CSV slice without error.
- [ ] No look-ahead: feed the same CSV twice, once truncated at bar N and once full-length —
      signals/state up through bar N must be identical between the two runs (a strategy with
      look-ahead would produce different early signals when it can "see" later data existing).
- [ ] Costs applied correctly on the first 3 trades — hand-verify spread/commission/swap per the
      engine spec's existing acceptance criterion (unchanged by this strategy's addition).
- [ ] Trade log leg count matches fill count, accounting for ladder legs (§9) — one Fill per leg
      closed, one trade_log row per Fill, not one row per Position for ladder trades.
- [ ] Parameter sweep runnable over at least one gate toggle and one SL/TP mode, not just a numeric
      field — confirms §11's requirement extends to categorical/boolean fields, not only numeric
      sweep targets like the engine spec's original SMA example.