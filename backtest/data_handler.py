"""
data_handler.py — CSV loader and bar iterator with no look-ahead.

Governed by:
- backtest_engine_spec.md §2 (DataHandler responsibility, method signatures, CSV format)
- ms_matrix_python_port_spec.md §14 (OANDA tick-count volume, weekend-gap semantics)
- QA checklist §15.2
"""

from typing import Optional
import pandas as pd

try:
    from .events import EventType, MarketEvent
except ImportError:
    from events import EventType, MarketEvent


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
        self.csv_path = csv_path
        self.symbol = symbol
        self.current_index: int = 0
        self._last_delivered_time: Optional[object] = None

        df = pd.read_csv(csv_path)

        # Normalize column headers (strip whitespace and lowercase)
        df.columns = df.columns.str.strip().str.lower()

        if "time" not in df.columns:
            raise ValueError(f"CSV '{csv_path}' is missing required 'time' column.")

        required_ohlc = ["open", "high", "low", "close"]
        for col in required_ohlc:
            if col not in df.columns:
                raise ValueError(f"CSV '{csv_path}' is missing required column '{col}'.")

        # Parse 'time' column as datetime and set as sorted index (timezone-naive UTC)
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time")
        df = df.sort_index(ascending=True)

        self.df: pd.DataFrame = df

    def get_bar(self) -> Optional[MarketEvent]:
        """
        Return MarketEvent for current bar, or None if exhausted.
        Increments internal index.
        """
        if not self.has_more():
            return None

        # Strictly inspect only the current row (zero look-ahead)
        bar_time = self.df.index[self.current_index]
        row = self.df.iloc[self.current_index]

        # Fail fast if OHLC contains missing/NaN values
        for col in ("open", "high", "low", "close"):
            val = row[col]
            if pd.isna(val):
                raise ValueError(
                    f"Missing or NaN OHLC data for column '{col}' at row index {self.current_index} "
                    f"(timestamp: {bar_time})."
                )

        # Handle volume: tick-count volume for OANDA; coerce missing or NaN to 0.0
        if "volume" in row:
            vol_raw = row["volume"]
            volume = 0.0 if pd.isna(vol_raw) else float(vol_raw)
        else:
            volume = 0.0

        event = MarketEvent(
            type=EventType.MARKET,
            timestamp=bar_time,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=volume,
        )

        self._last_delivered_time = bar_time
        self.current_index += 1

        return event

    def has_more(self) -> bool:
        """Return True if more bars remain."""
        return self.current_index < len(self.df)

    def current_time(self) -> Optional[object]:
        """Return timestamp of the bar just delivered."""
        return self._last_delivered_time
