"""Paper trading engine: opera el grid contra precios reales sin dinero real.

Reutiliza gridbot.strategy.simulator.GridSimulator — el mismo núcleo que el
backtester — para que "lo que se probó" y "lo que corre en vivo" sean
literalmente el mismo código. Persiste cada fill en la base de datos con la
versión del modelo, como pide el sistema de versionado (sección 50).
"""
from __future__ import annotations

import asyncio
import datetime as dt

from gridbot import __version__ as MODEL_VERSION
from gridbot.db.models import PaperFill, PaperSession, get_session
from gridbot.paper.feeds import PriceFeed
from gridbot.strategy.grid import GridConfig
from gridbot.strategy.simulator import GridSimulator


class PaperTradingEngine:
    def __init__(self, config: GridConfig, feed: PriceFeed, db_path: str = "sqlite:///gridbot.db"):
        self.config = config
        self.feed = feed
        self.db_path = db_path
        self.sim = None
        self.session_id = None
        self.last_price = None

    def _persist_session_start(self, current_price: float) -> None:
        import dataclasses

        db = get_session(self.db_path)
        ps = PaperSession(
            symbol=self.config.symbol,
            started_at=dt.datetime.utcnow(),
            status="running",
            config_json=dataclasses.asdict(self.config, dict_factory=_dict_factory),
            model_version=MODEL_VERSION,
        )
        db.add(ps)
        db.commit()
        self.session_id = ps.id
        db.close()
        print(
            f"[paper] sesión #{self.session_id} iniciada para {self.config.symbol} "
            f"@ {current_price:.4f} | rango [{self.config.lower:.4f}, {self.config.upper:.4f}] "
            f"num_grids={self.config.num_grids} inversión={self.config.total_investment}"
        )

    def _persist_fill(self, fill) -> None:
        db = get_session(self.db_path)
        db.add(
            PaperFill(
                session_id=self.session_id,
                timestamp=fill.timestamp if isinstance(fill.timestamp, dt.datetime) else dt.datetime.utcnow(),
                side=fill.side,
                price=fill.price,
                qty=fill.qty,
                slot_index=fill.slot_index,
                pnl=fill.pnl,
            )
        )
        db.commit()
        db.close()

    async def run(self, max_bars=None):
        n = 0
        async for ts, o, h, l, c in self.feed.stream():
            if self.sim is None:
                self.sim = GridSimulator(self.config, current_price=o)
                self._persist_session_start(o)

            fills = self.sim.process_bar(ts, o, h, l, c)
            for f in fills:
                self._persist_fill(f)
                tag = "COMPRA" if f.side == "buy" else "VENTA "
                pnl_txt = f" pnl={f.pnl:+.4f}" if f.pnl is not None else ""
                print(f"[paper][{self.config.symbol}] {tag} @ {f.price:.4f} qty={f.qty:.6f}{pnl_txt}")

            self.last_price = c
            equity = self.sim.equity(c)
            print(f"[paper][{self.config.symbol}] {ts} close={c:.4f} equity={equity:.2f}")

            if self.sim.stopped_out:
                print(f"[paper][{self.config.symbol}] DETENIDO: {self.sim.stop_reason}")
                break

            n += 1
            if max_bars is not None and n >= max_bars:
                break


def _dict_factory(items):
    from gridbot.strategy.grid import GridMode

    out = {}
    for k, v in items:
        out[k] = v.value if isinstance(v, GridMode) else v
    return out


async def _main_cli():
    import argparse
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from gridbot.data.loader import load_ohlcv_csv
    from gridbot.features.volatility import suggest_grid_range
    from gridbot.paper.feeds import LiveBinanceFeed, ReplayFeed

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--investment", type=float, default=1000.0)
    parser.add_argument("--mode", choices=["live", "replay"], default="live")
    parser.add_argument("--replay-csv", default=None)
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--max-bars", type=int, default=None)
    args = parser.parse_args()

    if args.mode == "replay":
        df, report = load_ohlcv_csv(args.replay_csv)
        df.attrs["symbol"] = args.symbol
        print(report.summary())
        suggestion = suggest_grid_range(df.iloc[: len(df) // 2])
        config = GridConfig(
            symbol=args.symbol,
            lower=suggestion.lower,
            upper=suggestion.upper,
            num_grids=suggestion.num_grids,
            total_investment=args.investment,
        )
        feed = ReplayFeed(df.iloc[len(df) // 2 :], speed=0.0)
    else:
        raise SystemExit(
            "Modo live: calibra antes el rango con scripts/run_backtest.py y arranca "
            "el paper trading en el servidor de despliegue (Railway/PC), donde sí hay "
            "acceso a api.binance.com. Este CLI es para pruebas en modo replay."
        )

    engine = PaperTradingEngine(config, feed)
    await engine.run(max_bars=args.max_bars)


if __name__ == "__main__":
    asyncio.run(_main_cli())
