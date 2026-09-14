"""Fuentes de precio para el paper trading engine.

`LiveBinanceFeed` es la fuente real: sondea el endpoint público de klines de
Binance (no requiere API key porque solo lee datos de mercado). Usa
data-api.binance.vision (el espejo público de solo-lectura de Binance) en
lugar de api.binance.com, porque este último devuelve HTTP 451 (bloqueo
geográfico) desde datacenters en EE.UU. como los de Render — el espejo
existe precisamente para evitar ese bloqueo regional sin necesitar API key.

`ReplayFeed` reproduce un CSV histórico como si fuera un stream en vivo —
sirve para probar el engine end-to-end sin depender de la red.
"""
from __future__ import annotations

import abc
import asyncio
import time
from collections.abc import AsyncIterator

import pandas as pd


class PriceFeed(abc.ABC):
    @abc.abstractmethod
    def stream(self) -> AsyncIterator[tuple[object, float, float, float, float]]:
        """Yields (timestamp, open, high, low, close)."""
        ...


class LiveBinanceFeed(PriceFeed):
    def __init__(self, symbol: str, interval: str = "1m", poll_seconds: float = 15.0):
        self.symbol = symbol
        self.interval = interval
        self.poll_seconds = poll_seconds
        self._last_open_time: int | None = None

    async def stream(self):
        import aiohttp

        # NOTA: api.binance.com devuelve HTTP 451 (bloqueo geográfico) desde
        # datacenters en EE.UU. como los de Render (Oregon). Usamos el espejo
        # público de datos de mercado de Binance (data-api.binance.vision),
        # pensado exactamente para este caso: solo lectura, sin API key,
        # sin el bloqueo regional del dominio principal.
        url = "https://data-api.binance.vision/api/v3/klines"
        params = {"symbol": self.symbol, "interval": self.interval, "limit": 2}
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
                except Exception as e:  # noqa: BLE001 — se registra y se reintenta, nunca se inventa un precio
                    print(f"[LiveBinanceFeed] error consultando Binance: {e}; reintentando en {self.poll_seconds}s")
                    await asyncio.sleep(self.poll_seconds)
                    continue

                # usamos la vela CERRADA más reciente (la [-2]) para no operar sobre una vela aún incompleta
                if len(data) >= 2:
                    k = data[-2]
                    open_time = k[0]
                    if open_time != self._last_open_time:
                        self._last_open_time = open_time
                        ts = pd.Timestamp(open_time, unit="ms", tz="UTC")
                        yield ts, float(k[1]), float(k[2]), float(k[3]), float(k[4])
                await asyncio.sleep(self.poll_seconds)


class ReplayFeed(PriceFeed):
    """Reproduce un CSV histórico como fuente de precios, para pruebas locales."""

    def __init__(self, df: pd.DataFrame, speed: float = 0.0):
        self.df = df.reset_index(drop=True)
        self.speed = speed  # segundos reales de pausa entre velas; 0 = sin pausa (test rápido)

    async def stream(self):
        for row in self.df.itertuples(index=False):
            yield row.timestamp, row.open, row.high, row.low, row.close
            if self.speed > 0:
                await asyncio.sleep(self.speed)
