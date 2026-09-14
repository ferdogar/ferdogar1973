"""Configuración de los bots activos.

Por defecto se genera a partir del último backtest (data/backtest_summary.csv
+ los rangos calibrados). En producción, `configs.json` es la fuente de
verdad editable — cámbialo ahí en vez de tocar código para ajustar
símbolos, capital o rango.
"""
from __future__ import annotations

import json
from pathlib import Path

from gridbot.strategy.grid import GridConfig, GridMode

CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs.json"


def load_configs() -> list[GridConfig]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"No existe {CONFIG_PATH}. Genera uno con scripts/generate_live_configs.py "
            "a partir de un backtest reciente, o créalo a mano."
        )
    raw = json.loads(CONFIG_PATH.read_text())
    configs = []
    for item in raw:
        item = dict(item)
        item["mode"] = GridMode(item.get("mode", "geometric"))
        configs.append(GridConfig(**item))
    return configs
