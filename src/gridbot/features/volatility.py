"""Features de volatilidad usadas para calibrar el rango y espaciado del grid.

Un grid bot gana dinero cuando el precio oscila DENTRO de un rango; pierde
(o se queda parado) si el precio tendencia fuera de rango. Por eso el rango
y el spacing no deben fijarse a mano sin evidencia: se derivan de ATR y de
la volatilidad histórica realizada.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def realized_volatility(df: pd.DataFrame, period: int = 24 * 7, annualize_periods_per_year: int = 24 * 365) -> pd.Series:
    """Volatilidad realizada (desviación típica de log-returns), anualizada."""
    log_ret = np.log(df["close"] / df["close"].shift(1))
    return log_ret.rolling(period).std() * np.sqrt(annualize_periods_per_year)


@dataclasses.dataclass
class GridRangeSuggestion:
    symbol: str
    reference_price: float
    lower: float
    upper: float
    num_grids: int
    grid_mode: str  # "geometric" | "arithmetic"
    atr_pct: float
    realized_vol_annual: float
    min_profitable_spacing_pct: float
    warnings: list[str]


def suggest_grid_range(
    df: pd.DataFrame,
    fee_rate: float = 0.001,
    lookback: int = 24 * 30,
    range_k_atr: float = 3.0,
    target_grids: int = 40,
) -> GridRangeSuggestion:
    """Sugiere lower/upper/num_grids a partir de ATR reciente.

    Esto es una ESTIMACIÓN basada en el comportamiento pasado reciente
    (lookback), no una predicción de hacia dónde irá el precio. Si el
    mercado rompe tendencia, el grid puede quedar completamente por encima
    o por debajo del rango — de ahí el stop-loss del risk engine.
    """
    warnings: list[str] = []
    window = df.tail(lookback).copy()
    if len(window) < lookback * 0.8:
        warnings.append(f"lookback incompleto ({len(window)}/{lookback} velas): estimación de rango poco fiable")

    ref_price = float(window["close"].iloc[-1])
    atr_series = atr(window, period=14)
    atr_val = float(atr_series.iloc[-1]) if not atr_series.iloc[-1] != atr_series.iloc[-1] else float(atr_series.dropna().iloc[-1])
    atr_pct = atr_val / ref_price

    vol_series = realized_volatility(window, period=min(24 * 7, len(window) - 1))
    vol_annual = float(vol_series.dropna().iloc[-1]) if vol_series.dropna().size else float("nan")

    half_range_pct = min(max(range_k_atr * atr_pct, 0.03), 0.35)  # clamp 3%-35%
    lower = ref_price * (1 - half_range_pct)
    upper = ref_price * (1 + half_range_pct)

    # spacing mínimo para que un round-trip cubra 2x fee + margen
    min_profitable_spacing_pct = 2 * fee_rate + 0.0015
    max_grids_by_fees = int((upper - lower) / (ref_price * min_profitable_spacing_pct))
    num_grids = max(5, min(target_grids, max_grids_by_fees))
    if max_grids_by_fees < target_grids:
        warnings.append(
            f"target_grids={target_grids} recortado a {num_grids} para que el spacing supere "
            f"2x fee ({fee_rate*100:.3f}%) + margen"
        )

    return GridRangeSuggestion(
        symbol=str(df.attrs.get("symbol", "")),
        reference_price=ref_price,
        lower=lower,
        upper=upper,
        num_grids=num_grids,
        grid_mode="geometric",
        atr_pct=atr_pct,
        realized_vol_annual=vol_annual,
        min_profitable_spacing_pct=min_profitable_spacing_pct,
        warnings=warnings,
  )
