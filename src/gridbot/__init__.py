"""Grid Trading Bot — quant-style spot grid engine.

Módulos:
  data      — carga y validación de datos OHLCV
  features  — volatilidad, ATR, sugerencia de rango de grid
  strategy  — motor de grid (niveles, órdenes, fills)
  backtest  — simulador event-driven + métricas
  risk      — position sizing, límites de capital, circuit breaker
  db        — modelos SQLAlchemy (SQLite por defecto)
  paper     — loop de paper trading en tiempo real (sin dinero real)
  dashboard — API FastAPI + HTML de estado
"""

__version__ = "0.1.0"
