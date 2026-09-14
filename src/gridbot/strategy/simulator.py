"""Núcleo de simulación de fills del grid, compartido por backtest y paper trading.

Aislar esta lógica evita el riesgo más común al construir un bot: que el
backtest y el motor en vivo diverjan silenciosamente porque alguien solo
actualizó una de las dos copias. Ambos usan `GridSimulator.process_bar`.
"""
from __future__ import annotations

import dataclasses

from gridbot.strategy.grid import GridConfig, GridSlot, build_initial_slots, seed_cost


@dataclasses.dataclass
class Fill:
    timestamp: object  # pd.Timestamp o datetime
    side: str  # "buy" | "sell"
    price: float
    qty: float
    slot_index: int
    pnl: float | None = None


class GridSimulator:
    def __init__(self, config: GridConfig, current_price: float):
        self.config = config
        self.slots: list[GridSlot] = build_initial_slots(config, current_price)
        self.slot_by_index: dict[int, GridSlot] = {s.index: s for s in self.slots}
        self.lines_by_slot: dict[int, tuple[float, float]] = {s.index: (s.lower, s.upper) for s in self.slots}
        self.quote_balance = config.total_investment - seed_cost(self.slots)
        self.stopped_out = False
        self.stop_reason: str | None = None
        self._sl_price = config.stop_loss_price()

    def holding_value(self, mark_price: float) -> float:
        return sum(s.qty * mark_price for s in self.slot_by_index.values() if s.holding)

    def equity(self, mark_price: float) -> float:
        return self.quote_balance + self.holding_value(mark_price)

    def process_bar(self, ts, o: float, h: float, l: float, c: float) -> list[Fill]:
        """Procesa una vela (u observación de precio con o=h=l=c para ticks) y devuelve los fills."""
        fills: list[Fill] = []
        if self.stopped_out:
            return fills

        touched: list[tuple[float, str, int]] = []
        for idx, (lo, hi) in self.lines_by_slot.items():
            if l <= lo <= h:
                touched.append((lo, "lower", idx))
            if l <= hi <= h:
                touched.append((hi, "upper", idx))

        bullish = c >= o
        touched.sort(key=lambda x: x[0], reverse=not bullish)

        fee_rate = self.config.fee_rate
        for price, boundary, idx in touched:
            slot = self.slot_by_index[idx]
            if boundary == "lower" and not slot.holding:
                cost = slot.qty * price
                total_cost = cost * (1 + fee_rate)
                if self.quote_balance >= total_cost:
                    self.quote_balance -= total_cost
                    slot.holding = True
                    slot.entry_price = price
                    slot.entry_cost = total_cost
                    fills.append(Fill(timestamp=ts, side="buy", price=price, qty=slot.qty, slot_index=idx))
            elif boundary == "upper" and slot.holding:
                proceeds = slot.qty * price
                net_proceeds = proceeds * (1 - fee_rate)
                cost_basis = slot.entry_cost if slot.entry_cost is not None else slot.qty * (slot.entry_price or price)
                pnl = net_proceeds - cost_basis
                self.quote_balance += net_proceeds
                slot.holding = False
                slot.entry_price = None
                slot.entry_cost = None
                fills.append(Fill(timestamp=ts, side="sell", price=price, qty=slot.qty, slot_index=idx, pnl=pnl))

        if self._sl_price is not None and c < self._sl_price and not self.stopped_out:
            liquidation_value = self.holding_value(c)
            liquidation_fee = liquidation_value * fee_rate
            self.quote_balance += liquidation_value - liquidation_fee
            for s in self.slot_by_index.values():
                s.holding = False
                s.entry_price = None
                s.entry_cost = None
            self.stopped_out = True
            self.stop_reason = f"stop-loss: precio de cierre {c:.4f} < {self._sl_price:.4f}"

        return fills
