"""Dashboard + orquestador. Al arrancar, lanza un PaperTradingEngine por cada
símbolo en configs.json (fuente de precios: Binance en vivo) como tareas de
fondo, y expone su estado vía API + una página HTML simple.

Arranque local:
    PYTHONPATH=src uvicorn gridbot.dashboard.app:app --host 0.0.0.0 --port 8000

En Railway, ver Dockerfile / Procfile del proyecto.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from gridbot import __version__ as MODEL_VERSION
from gridbot.config import load_configs
from gridbot.db.models import PaperFill, PaperSession, get_session
from gridbot.paper.engine import PaperTradingEngine
from gridbot.paper.feeds import LiveBinanceFeed

DB_PATH = "sqlite:///gridbot.db"

_engines: dict[str, PaperTradingEngine] = {}
_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        configs = load_configs()
    except FileNotFoundError as e:
        print(f"[dashboard] {e}")
        configs = []

    for cfg in configs:
        feed = LiveBinanceFeed(symbol=cfg.symbol, interval="1m", poll_seconds=15.0)
        engine = PaperTradingEngine(cfg, feed, db_path=DB_PATH)
        _engines[cfg.symbol] = engine
        _tasks.append(asyncio.create_task(engine.run()))
        print(f"[dashboard] lanzado paper trading para {cfg.symbol}")

    yield

    for t in _tasks:
        t.cancel()


app = FastAPI(title="Grid Trading Bot Dashboard", lifespan=lifespan)


@app.get("/api/status")
def status():
    db = get_session(DB_PATH)
    out = []
    for symbol, engine in _engines.items():
        sim = engine.sim
        row = {
            "symbol": symbol,
            "started": sim is not None,
            "stopped_out": sim.stopped_out if sim else False,
            "stop_reason": sim.stop_reason if sim else None,
            "quote_balance": round(sim.quote_balance, 4) if sim else None,
            "config": {
                "lower": engine.config.lower,
                "upper": engine.config.upper,
                "num_grids": engine.config.num_grids,
                "total_investment": engine.config.total_investment,
            },
            "model_version": MODEL_VERSION,
        }
        out.append(row)
    db.close()
    return {"bots": out, "server_time": dt.datetime.utcnow().isoformat()}


@app.get("/api/trades/{symbol}")
def trades(symbol: str, limit: int = 50):
    db = get_session(DB_PATH)
    sessions = (
        db.query(PaperSession)
        .filter(PaperSession.symbol == symbol)
        .order_by(PaperSession.id.desc())
        .all()
    )
    if not sessions:
        db.close()
        return {"symbol": symbol, "fills": []}
    session_id = sessions[0].id
    fills = (
        db.query(PaperFill)
        .filter(PaperFill.session_id == session_id)
        .order_by(PaperFill.id.desc())
        .limit(limit)
        .all()
    )
    out = [
        {
            "timestamp": f.timestamp.isoformat(),
            "side": f.side,
            "price": f.price,
            "qty": f.qty,
            "pnl": f.pnl,
        }
        for f in fills
    ]
    db.close()
    return {"symbol": symbol, "fills": out}


@app.get("/", response_class=HTMLResponse)
def dashboard_page():
    return _HTML


_HTML = """
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Grid Trading Bot</title>
<style>
  body { font-family: system-ui, sans-serif; background:#0b0e14; color:#e6e6e6; margin:0; padding:24px; }
  h1 { font-size: 20px; margin-bottom: 4px; }
  .sub { color:#8a8f98; font-size:13px; margin-bottom:20px; }
  table { width:100%; border-collapse:collapse; margin-top:12px; }
  th, td { text-align:left; padding:8px 10px; border-bottom:1px solid #1f2430; font-size:14px; }
  th { color:#8a8f98; font-weight:500; }
  .ok { color:#3ddc97; } .bad { color:#ff6767; }
  .card { background:#11151d; border:1px solid #1f2430; border-radius:10px; padding:16px; margin-bottom:16px; }
</style>
</head>
<body>
  <h1>Grid Trading Bot — Paper Trading</h1>
  <div class="sub">Sin dinero real. Datos en vivo de Binance. Se actualiza cada 10s.</div>
  <div id="root"></div>
<script>
async function refresh() {
  const res = await fetch('/api/status');
  const data = await res.json();
  const root = document.getElementById('root');
  root.innerHTML = data.bots.map(b => `
    <div class="card">
      <h3>${b.symbol} ${b.stopped_out ? '<span class="bad">DETENIDO</span>' : '<span class="ok">activo</span>'}</h3>
      <div>Rango: ${b.config.lower.toFixed(4)} - ${b.config.upper.toFixed(4)} | Grids: ${b.config.num_grids} | Inversión: ${b.config.total_investment}</div>
      <div>Balance en quote: ${b.quote_balance ?? '—'}</div>
      ${b.stop_reason ? `<div class="bad">${b.stop_reason}</div>` : ''}
    </div>
  `).join('') || '<p>No hay bots configurados. Genera configs.json con scripts/generate_live_configs.py</p>';
}
refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>
"""
