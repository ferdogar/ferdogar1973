"""Backtester event-driven para el Grid Trading Bot.

LIMITACIÓN EXPLÍCITA (principio de honestidad, sección 72 del sistema del
usuario): no tenemos datos tick-by-tick, solo velas OHLCV. Para simular qué
líneas de grid se tocan dentro de una vela, `GridSimulator` barre las líneas
cruzadas entre `low` y `high` en el orden low→high si la vela es alcista
(close>=open) o high→low si es bajista. Esto es una APROXIMACIÓN razonable a
resolución horaria, pero no reconstruye el order flow real intrabar. Los
resultados deben tratarse como ESTIMACIÓN, validada después con paper
trading antes de arriesgar capital real.

Este motor reutiliza `gridbot.strategy.simulator.GridSimulator`, el mismo
núcleo que usa el paper trading engine, para que backtest y ejecución en
vivo no puedan divergir silenciosamente.
"""
from __future__ import annotations

import dataclasses

import pandas as pd

from gridbot.strategy.grid import GridConfig
from gridbot.strategy.simulator import Fill, GridSimulator


@dataclasses.dataclass
class BacktestResult:
    symbol: str
    config: GridConfig
    fills: list[Fill]
    equity_curve: pd.DataFrame  # columns: timestamp, equity, price
    stopped_out: bool
    stop_reason: str | None

    def metrics(self) -> dict:
        sells = [f for f in self.fills if f.side == "sell"]
        n_trades = len(sells)
        wins = [f for f in sells if (f.pnl or 0) > 0]
        gross_profit = sum(f.pnl for f in sells if (f.pnl or 0) > 0)
        gross_loss = sum(f.pnl for f in sells if (f.pnl or 0) < 0)
        total_pnl = sum(f.pnl or 0 for f in sells)

        eq = self.equity_curve["equity"]
        running_max = eq.cummax()
        drawdown = (eq - running_max) / running_max
        max_drawdown = float(drawdown.min()) if len(drawdown) else 0.0

        initial_equity = float(eq.iloc[0]) if len(eq) else self.config.total_investment
        final_equity = float(eq.iloc[-1]) if len(eq) else initial_equity
        roi = (final_equity - initial_equity) / initial_equity if initial_equity else 0.0
        unrealized_pnl = (final_equity - initial_equity) - total_pnl  # inventario aún abierto al cierre, marcado a mercado

        n_days = 0.0
        if len(self.equity_curve) > 1:
            n_days = (self.equity_curve["timestamp"].iloc[-1] - self.equity_curve["timestamp"].iloc[0]).total_seconds() / 86400
        yield_annualized = roi * (365 / n_days) if n_days > 0 else float("nan")

        profit_factor = (gross_profit / abs(gross_loss)) if gross_loss < 0 else float("inf") if gross_profit > 0 else 0.0

        return {
            "symbol": self.symbol,
            "n_trades": n_trades,
            "win_rate": (len(wins) / n_trades) if n_trades else 0.0,
            "realized_pnl_quote": total_pnl,
            "unrealized_pnl_quote": unrealized_pnl,
            "roi_pct": roi * 100,
            "yield_annualized_pct": yield_annualized * 100,
            "max_drawdown_pct": max_drawdown * 100,
            "profit_factor": profit_factor,
            "final_equity": final_equity,
            "initial_equity": initial_equity,
            "stopped_out": self.stopped_out,
            "stop_reason": self.stop_reason,
            "n_days": round(n_days, 2),
        }


def run_backtest(df: pd.DataFrame, config: GridConfig) -> BacktestResult:
    df = df.reset_index(drop=True)
    current_price = float(df["close"].iloc[0])
    sim = GridSimulator(config, current_price)

    fills: list[Fill] = []
    equity_rows = []

    for row in df.itertuples(index=False):
        bar_fills = sim.process_bar(row.timestamp, row.open, row.high, row.low, row.close)
        fills.extend(bar_fills)
        equity_rows.append({"timestamp": row.timestamp, "equity": sim.equity(row.close), "price": row.close})
        if sim.stopped_out:
            break

    equity_curve = pd.DataFrame(equity_rows)
    return BacktestResult(
        symbol=config.symbol,
        config=config,
        fills=fills,
        equity_curve=equity_curve,
        stopped_out=sim.stopped_out,
        stop_reason=sim.stop_reason,
      )
