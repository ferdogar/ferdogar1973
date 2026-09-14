"""Motor de estrategia Grid Trading (spot, long-only).

Modelo: N+1 líneas de precio entre `lower` y `upper`. Cada intervalo entre
dos líneas adyacentes es un "slot": cuando el precio toca la línea inferior
del slot se compra `qty`; cuando toca la línea superior se vende esa misma
`qty`, materializando el spread del intervalo menos comisiones. El slot
vuelve a quedar disponible para comprar de nuevo.

Esto es idéntico al mecanismo del Spot Grid de Binance (equal-quantity grid).
"""
from __future__ import annotations

import dataclasses
from enum import Enum


class GridMode(str, Enum):
    ARITHMETIC = "arithmetic"
    GEOMETRIC = "geometric"


@dataclasses.dataclass
class GridConfig:
    symbol: str
    lower: float
    upper: float
    num_grids: int
    total_investment: float  # en moneda cotizada (ej. USDT)
    mode: GridMode = GridMode.GEOMETRIC
    fee_rate: float = 0.001  # 0.1% Binance spot estándar (sin BNB discount)
    stop_loss_pct: float | None = 0.15  # cierra el bot si el precio cae stop_loss_pct por debajo de `lower`
    take_profit_price: float | None = None  # cierra el bot y liquida si el precio supera este nivel

    def __post_init__(self):
        if self.lower <= 0 or self.upper <= self.lower:
            raise ValueError("Rango de grid inválido: se requiere 0 < lower < upper")
        if self.num_grids < 2:
            raise ValueError("num_grids debe ser >= 2")
        if self.total_investment <= 0:
            raise ValueError("total_investment debe ser > 0")

    def grid_lines(self) -> list[float]:
        n = self.num_grids
        if self.mode == GridMode.ARITHMETIC:
            step = (self.upper - self.lower) / n
            return [self.lower + i * step for i in range(n + 1)]
        else:
            ratio = (self.upper / self.lower) ** (1 / n)
            return [self.lower * (ratio**i) for i in range(n + 1)]

    def qty_per_grid(self, reference_price: float) -> float:
        """Cantidad (en base asset) por slot, usando equal-quantity grid.

        total_investment se reparte entre num_grids slots, valorados al
        precio de referencia (precio en el momento de crear el bot).
        """
        quote_per_grid = self.total_investment / self.num_grids
        return quote_per_grid / reference_price

    def stop_loss_price(self) -> float | None:
        if self.stop_loss_pct is None:
            return None
        return self.lower * (1 - self.stop_loss_pct)


@dataclasses.dataclass
class GridSlot:
    index: int
    lower: float
    upper: float
    qty: float
    holding: bool  # True si ya compramos en `lower` y esperamos vender en `upper`
    entry_price: float | None = None  # precio real de compra si holding=True
    entry_cost: float | None = None  # coste total pagado (qty*precio + comisión de compra)


def build_initial_slots(config: GridConfig, current_price: float) -> list[GridSlot]:
    """Crea los slots iniciales.

    Para los slots por debajo del precio actual: vacíos, esperando comprar en `lower`.
    Para los slots por encima: se asume inventario inicial (seed) comprado a
    `current_price`, para poder vender en `upper` — igual que hace Binance al
    arrancar un Spot Grid (compra inicial para poblar las órdenes de venta).
    La compra seed paga comisión igual que cualquier otra compra.
    """
    lines = config.grid_lines()
    qty = config.qty_per_grid(current_price)
    slots: list[GridSlot] = []
    for i in range(config.num_grids):
        lo, hi = lines[i], lines[i + 1]
        if hi <= current_price:
            slots.append(GridSlot(index=i, lower=lo, upper=hi, qty=qty, holding=False))
        elif lo >= current_price:
            cost = qty * current_price
            entry_cost = cost * (1 + config.fee_rate)
            slots.append(
                GridSlot(index=i, lower=lo, upper=hi, qty=qty, holding=True, entry_price=current_price, entry_cost=entry_cost)
            )
        else:
            # el precio actual cae dentro de este slot: lo tratamos como vacío
            # (evita comprar/vender al mismo precio de arranque)
            slots.append(GridSlot(index=i, lower=lo, upper=hi, qty=qty, holding=False))
    return slots


def seed_cost(slots: list[GridSlot]) -> float:
    """Coste total (en quote currency, incluyendo comisión) de la compra inicial usada para poblar sells."""
    return sum(s.entry_cost for s in slots if s.holding and s.entry_cost)
