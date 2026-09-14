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

_engines = {}
_tasks = []


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
    total_equity = 0.0
    total_invested = 0.0
    for symbol, engine in _engines.items():
        sim = engine.sim
        realized_pnl = None
        equity = None
        roi_pct = None
        n_fills = 0
        if sim is not None:
            realized_pnl = (
                db.query(PaperFill)
                .filter(PaperFill.session_id == engine.session_id, PaperFill.pnl.isnot(None))
                .with_entities(PaperFill.pnl)
                .all()
            )
            realized_pnl = sum(p[0] for p in realized_pnl) if realized_pnl else 0.0
            n_fills = (
                db.query(PaperFill).filter(PaperFill.session_id == engine.session_id).count()
            )
            mark_price = engine.last_price if engine.last_price is not None else sim.slots[0].lower
            equity = sim.equity(mark_price)
            roi_pct = (equity - engine.config.total_investment) / engine.config.total_investment * 100
            total_equity += equity
            total_invested += engine.config.total_investment

        row = {
            "symbol": symbol,
            "started": sim is not None,
            "stopped_out": sim.stopped_out if sim else False,
            "stop_reason": sim.stop_reason if sim else None,
            "quote_balance": round(sim.quote_balance, 4) if sim else None,
            "last_price": engine.last_price,
            "equity": round(equity, 4) if equity is not None else None,
            "realized_pnl_quote": round(realized_pnl, 4) if realized_pnl is not None else None,
            "roi_pct": round(roi_pct, 3) if roi_pct is not None else None,
            "n_fills": n_fills,
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

    portfolio_roi_pct = (
        round((total_equity - total_invested) / total_invested * 100, 3) if total_invested else None
    )
    return {
        "bots": out,
        "portfolio": {
            "total_equity": round(total_equity, 2) if total_invested else None,
            "total_invested": round(total_invested, 2) if total_invested else None,
            "roi_pct": portfolio_roi_pct,
        },
        "server_time": dt.datetime.utcnow().isoformat(),
    }


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
  <div id="portfolio"></div>
  <div id="root"></div>
<script>
function roiClass(v) {
  if (v === null || v === undefined) return '';
  return v >= 0 ? 'ok' : 'bad';
}
function fmtRoi(v) {
  if (v === null || v === undefined) return '—';
  return (v >= 0 ? '+' : '') + v.toFixed(3) + '%';
}
async function refresh() {
  const res = await fetch('/api/status');
  const data = await res.json();
  const p = data.portfolio || {};
  const portfolioEl = document.getElementById('portfolio');
  if (p.total_invested) {
    portfolioEl.innerHTML = `
      <div class="card">
        <h3>Portfolio total</h3>
        <div>Invertido: ${p.total_invested} | Equity actual: ${p.total_equity}</div>
        <div>ROI agregado: <span class="${roiClass(p.roi_pct)}">${fmtRoi(p.roi_pct)}</span></div>
        <div class="sub">ROI &gt; 0% = rentable ahora mismo (a precio de mercado, incluye inventario aún no vendido). Con muestra pequeña (pocos días) esto es orientativo, no concluyente — sección 29 del sistema.</div>
      </div>`;
  } else {
    portfolioEl.innerHTML = '';
  }
  const root = document.getElementById('root');
  root.innerHTML = data.bots.map(b => `
    <div class="card">
      <h3>${b.symbol} ${b.stopped_out ? '<span class="bad">DETENIDO</span>' : '<span class="ok">activo</span>'}</h3>
      <div>Rango: ${b.config.lower.toFixed(4)} - ${b.config.upper.toFixed(4)} | Grids: ${b.config.num_grids} | Inversión: ${b.config.total_investment}</div>
      <div>Precio actual: ${b.last_price ?? '—'} | Balance en quote: ${b.quote_balance ?? '—'}</div>
      <div>Equity: ${b.equity ?? '—'} | ROI: <span class="${roiClass(b.roi_pct)}">${fmtRoi(b.roi_pct)}</span></div>
      <div>PnL realizado (ventas cerradas): ${b.realized_pnl_quote ?? '—'} | Fills totales: ${b.n_fills ?? 0}</div>
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
