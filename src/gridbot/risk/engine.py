"""Risk engine: cuánto capital asignar a cada bot de grid y cuándo detenerlo.

Principios aplicados (sección 39-40 del sistema del usuario):
- nunca aumentar el stake solo por confianza subjetiva
- limitar la correlación entre pares (BTC/ETH/BNB/SOL se mueven muy
  correlacionados — no son 4 apuestas independientes, son casi una)
- circuit breaker por drawdown
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd


@dataclasses.dataclass
class RiskLimits:
    max_capital_per_symbol_pct: float = 0.35  # máx 35% del capital total en un solo par
    max_total_deployed_pct: float = 0.90  # dejar siempre colchón de liquidez
    max_correlation_group_pct: float = 0.60  # tope conjunto para pares con corr > threshold
    correlation_threshold: float = 0.75
    max_drawdown_circuit_breaker_pct: float = 0.20  # pausar todo el sistema si el DD agregado supera esto


def pairwise_correlation(price_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Correlación de retornos horarios entre símbolos, para no sobre-concentrar capital."""
    returns = {}
    for symbol, df in price_frames.items():
        returns[symbol] = np.log(df["close"] / df["close"].shift(1))
    ret_df = pd.DataFrame(returns).dropna()
    return ret_df.corr()


def allocate_capital(
    total_capital: float,
    symbols: list[str],
    correlation_matrix: pd.DataFrame,
    limits: RiskLimits,
) -> dict[str, float]:
    """Reparte capital entre símbolos respetando límites de concentración.

    Método simple y auditable (no una optimización de Markowitz completa,
    que exigiría más histórico del que tenemos): partir de partes iguales,
    recortar cualquier símbolo por encima de max_capital_per_symbol_pct, y
    recortar el conjunto de símbolos altamente correlacionados entre sí
    por encima de max_correlation_group_pct.
    """
    n = len(symbols)
    if n == 0:
        return {}
    base = {s: total_capital / n for s in symbols}

    cap_per_symbol = total_capital * limits.max_capital_per_symbol_pct
    for s in symbols:
        base[s] = min(base[s], cap_per_symbol)

    # agrupar por correlación alta y limitar la exposición conjunta
    visited = set()
    groups: list[list[str]] = []
    for s in symbols:
        if s in visited:
            continue
        group = [s]
        visited.add(s)
        for other in symbols:
            if other == s or other in visited:
                continue
            corr = correlation_matrix.loc[s, other] if s in correlation_matrix.index and other in correlation_matrix.columns else 0
            if corr >= limits.correlation_threshold:
                group.append(other)
                visited.add(other)
        groups.append(group)

    for group in groups:
        if len(group) <= 1:
            continue
        group_cap = total_capital * limits.max_correlation_group_pct
        group_total = sum(base[s] for s in group)
        if group_total > group_cap:
            scale = group_cap / group_total
            for s in group:
                base[s] *= scale

    total_allocated = sum(base.values())
    max_deployed = total_capital * limits.max_total_deployed_pct
    if total_allocated > max_deployed:
        scale = max_deployed / total_allocated
        for s in base:
            base[s] *= scale

    return base


def check_circuit_breaker(equity_curves: dict[str, pd.DataFrame], limits: RiskLimits) -> tuple[bool, str | None]:
    """Evalúa si el drawdown agregado del portfolio supera el límite y hay que pausar."""
    combined = None
    for symbol, ec in equity_curves.items():
        s = ec.set_index("timestamp")["equity"]
        combined = s if combined is None else combined.add(s, fill_value=0)
    if combined is None or combined.empty:
        return False, None
    running_max = combined.cummax()
    dd = (combined - running_max) / running_max
    max_dd = float(dd.min())
    if abs(max_dd) >= limits.max_drawdown_circuit_breaker_pct:
        return True, f"drawdown agregado {max_dd*100:.1f}% >= límite {limits.max_drawdown_circuit_breaker_pct*100:.1f}%"
    return False, None
