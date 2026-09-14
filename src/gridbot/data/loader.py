"""Carga y validación de datos OHLCV.

Principio (ver sistema maestro del usuario, sección DATA QUALITY):
antes de modelar hay que determinar la calidad de los datos.
Clasificamos cada dataset en A/B/C/D y lo dejamos explícito en el resultado,
en vez de asumir silenciosamente que los datos son perfectos.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pandas as pd


@dataclasses.dataclass
class DataQualityReport:
    symbol: str
    n_rows: int
    start: pd.Timestamp
    end: pd.Timestamp
    expected_rows: int
    missing_rows: int
    duplicate_rows: int
    gaps: list[tuple[pd.Timestamp, pd.Timestamp]]
    grade: str  # A/B/C/D
    notes: list[str]

    def summary(self) -> str:
        return (
            f"[{self.symbol}] grade={self.grade} rows={self.n_rows}/{self.expected_rows} "
            f"missing={self.missing_rows} dupes={self.duplicate_rows} gaps={len(self.gaps)} "
            f"rango={self.start} -> {self.end}"
        )


def load_ohlcv_csv(path: str | Path, timeframe_minutes: int = 60) -> tuple[pd.DataFrame, DataQualityReport]:
    """Carga un CSV timestamp,open,high,low,close,volume y audita su calidad.

    NUNCA se rellenan huecos con datos inventados: los gaps se reportan,
    no se interpolan silenciosamente. El caller decide qué hacer con ellos
    (recortar el rango, o reducir la confianza del backtest).
    """
    path = Path(path)
    symbol = path.stem.split("_")[0]
    df = pd.read_csv(path)
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing_cols = required - set(df.columns)
    if missing_cols:
        raise ValueError(f"Faltan columnas {missing_cols} en {path}")

    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)

    duplicate_rows = int(df.duplicated(subset="timestamp").sum())
    df = df.drop_duplicates(subset="timestamp", keep="first").reset_index(drop=True)

    # sanity checks de precios: high >= low, high >= open/close, low <= open/close
    bad_bars = df[
        (df["high"] < df["low"])
        | (df["high"] < df["open"])
        | (df["high"] < df["close"])
        | (df["low"] > df["open"])
        | (df["low"] > df["close"])
        | (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
    ]
    notes = []
    if len(bad_bars) > 0:
        notes.append(f"{len(bad_bars)} velas con OHLC inconsistente eliminadas")
        df = df.drop(bad_bars.index).reset_index(drop=True)

    freq = pd.Timedelta(minutes=timeframe_minutes)
    start, end = df["timestamp"].iloc[0], df["timestamp"].iloc[-1]
    expected_rows = int((end - start) / freq) + 1

    full_index = pd.date_range(start, end, freq=freq, tz="UTC")
    present = set(df["timestamp"])
    missing_ts = [t for t in full_index if t not in present]

    gaps: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    if missing_ts:
        run_start = missing_ts[0]
        prev = missing_ts[0]
        for t in missing_ts[1:]:
            if t - prev > freq:
                gaps.append((run_start, prev))
                run_start = t
            prev = t
        gaps.append((run_start, prev))

    missing_rows = len(missing_ts)
    missing_pct = missing_rows / expected_rows if expected_rows else 1.0

    if missing_pct == 0 and duplicate_rows == 0 and len(bad_bars) == 0:
        grade = "A"
    elif missing_pct < 0.005 and duplicate_rows < 5:
        grade = "B"
    elif missing_pct < 0.03:
        grade = "C"
        notes.append("cobertura <97%: reducir confianza de resultados derivados")
    else:
        grade = "D"
        notes.append("datos insuficientes: NO usar para decisiones de alta confianza")

    report = DataQualityReport(
        symbol=symbol,
        n_rows=len(df),
        start=start,
        end=end,
        expected_rows=expected_rows,
        missing_rows=missing_rows,
        duplicate_rows=duplicate_rows,
        gaps=gaps,
        grade=grade,
        notes=notes,
    )
    return df, report
