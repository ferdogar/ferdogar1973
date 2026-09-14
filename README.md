# Grid Trading Bot

Bot de Grid Trading (spot, long-only) con arquitectura de research cuantitativo:
ingesta y auditoría de datos, motor de features de volatilidad, backtester
event-driven con walk-forward, risk engine (position sizing + correlación +
circuit breaker), motor de paper trading en vivo y dashboard.

**Estado actual: solo backtest + paper trading (sin dinero real).** No ejecuta
órdenes reales en Binance. Es el paso previo obligatorio antes de arriesgar
capital, tal como se pidió.

## Arquitectura

```
data/*.csv (histórico)
   -> gridbot.data.loader          (carga + audita calidad A/B/C/D)
   -> gridbot.features.volatility  (ATR, vol. realizada -> rango sugerido de grid)
   -> gridbot.strategy.grid        (GridConfig, niveles, qty por slot)
   -> gridbot.strategy.simulator   (núcleo de fills, compartido backtest/paper)
   -> gridbot.backtest.engine      (walk-forward train/test + métricas)
   -> gridbot.risk.engine          (asignación de capital, correlación, circuit breaker)
   -> gridbot.db.models            (SQLite: backtest_runs, trades, paper_sessions, paper_fills)
   -> gridbot.paper.engine         (paper trading contra precio real de Binance)
   -> gridbot.dashboard.app        (FastAPI: API + HTML de estado)
```

## Por qué Grid Trading

Gana dinero cuando el precio oscila dentro de un rango (mercado lateral);
pierde exposición (no dinero, pero se queda "parado" o con inventario no
vendido) si el precio rompe tendencia fuera del rango. Por eso:

- El rango y el número de grids **no se fijan a mano**: se derivan de ATR y
  volatilidad realizada reciente (`suggest_grid_range`).
- Hay un **stop-loss** (`stop_loss_pct`, 15% por defecto por debajo del
  límite inferior) que liquida el inventario si el mercado rompe a la baja.
- El **risk engine** limita cuánto capital va a un solo símbolo y cuánto va
  conjuntamente a símbolos con correlación alta (BTC/ETH/BNB/SOL rondan
  0.77-0.87 de correlación en el histórico probado — no son apuestas
  independientes).

## Limitaciones honestas (léelas antes de confiar en los resultados)

1. **Backtest con velas 1h, no tick-by-tick.** La ejecución intrabar se
   aproxima barriendo las líneas de grid tocadas en el rango [low, high] de
   cada vela. Es razonable a esta resolución pero no reconstruye el order
   book real.
2. **Un solo split walk-forward (60% train / 40% test) sobre 90 días.**
   Es un punto de partida, no una validación robusta — para eso hace falta
   walk-forward con múltiples ventanas rodantes y más histórico (habla con
   el equipo/o pídeme extenderlo a 6-12 meses cuando quieras ir en serio).
3. **100% win rate en el backtest es esperable por construcción** (cada
   round-trip completo de un grid es rentable por diseño si el spacing >
   2×fee), **no es evidencia de que la estrategia sea rentable en conjunto**:
   el riesgo real es el inventario que queda comprado y no se vende si el
   precio no vuelve a subir (unrealized_pnl_quote en las métricas).
4. **Todo lo anterior es ESTIMACIÓN basada en datos pasados recientes**, no
   predicción. Un cambio de régimen de mercado (ruptura de rango) puede
   hacer que el bot pierda dinero o quede inmovilizado.

## Requisitos para pasar a producción (dinero real)

Actualmente NO se ejecutan órdenes reales — solo se simulan contra el precio
real de mercado. Para pasar a real habría que:

1. Añadir un módulo `gridbot/live/executor.py` que use la API autenticada de
   Binance (API key/secret **solo en variables de entorno**, nunca en
   código) con permisos de solo trading (sin retiro).
2. Validar el paper trading en vivo durante al menos varias semanas y
   comparar sus métricas contra el backtest (model drift, sección 31).
3. Empezar con capital pequeño y stake conservador (Fractional Kelly o
   flat), nunca todo el capital calibrado de golpe.

## Uso local

```bash
pip install -r requirements.txt

# 1) Backtest walk-forward (usa los CSV en data/)
PYTHONPATH=src python3 scripts/run_backtest.py

# 2) Generar configs.json (rango calibrado) para paper trading
PYTHONPATH=src python3 scripts/generate_live_configs.py

# 3a) Paper trading contra un CSV histórico (prueba sin red)
PYTHONPATH=src python3 -m gridbot.paper.engine --symbol BTCUSDT --investment 1000 \
  --mode replay --replay-csv data/BTCUSDT_1h_90d.csv --max-bars 500

# 3b) Dashboard + paper trading en vivo (necesita salida a api.binance.com)
PYTHONPATH=src uvicorn gridbot.dashboard.app:app --host 0.0.0.0 --port 8000
```

## Despliegue (Docker / Render / Railway)

El repo incluye un `Dockerfile` autosuficiente: instala dependencias, copia
`src/` y `configs.json`, y arranca el dashboard con uvicorn respetando la
variable `PORT` que asigne la plataforma.

En Render.com (free tier): New Web Service -> conectar este repo -> Render
detecta el Dockerfile automáticamente -> Create Web Service.

En Railway: `railway login && railway init && railway up` (usa `railway.json`).

**Importante — persistencia de la base de datos:** por defecto se usa SQLite
local al contenedor (`gridbot.db`), que se **pierde en cada redeploy**. Para
producción real, monta un volumen persistente en `/app/data` o (mejor) cambia
`DB_PATH` a un Postgres gestionado — el esquema en `gridbot/db/models.py` es
compatible con Postgres sin cambios (SQLAlchemy).

## Base de datos

Cada backtest y cada fill de paper trading se guarda con `model_version` y
`data_grade`, para poder auditar después qué versión del modelo generó qué
resultado (sección de versionado del sistema del usuario).
