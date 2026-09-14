"""Esquema de base de datos: cada backtest / trade queda versionado y auditable.

Campos mínimos según sección 49-50 del sistema del usuario: fecha, símbolo,
mercado, selección, precio, timestamp, probabilidad/edge (aquí: métricas del
grid), stake, resultado, modelo usado, versión del modelo y de los datos.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    data_start = Column(DateTime)
    data_end = Column(DateTime)
    data_grade = Column(String)  # A/B/C/D
    model_version = Column(String, default="gridbot-0.1.0")
    config_json = Column(JSON)  # GridConfig serializado
    metrics_json = Column(JSON)  # output de BacktestResult.metrics()

    trades = relationship("TradeRecord", back_populates="run", cascade="all, delete-orphan")


class TradeRecord(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("backtest_runs.id"))
    timestamp = Column(DateTime, nullable=False)
    side = Column(String, nullable=False)  # buy | sell
    price = Column(Float, nullable=False)
    qty = Column(Float, nullable=False)
    slot_index = Column(Integer)
    pnl = Column(Float, nullable=True)

    run = relationship("BacktestRun", back_populates="trades")


class PaperSession(Base):
    """Sesión de paper trading en vivo (sin dinero real)."""

    __tablename__ = "paper_sessions"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    started_at = Column(DateTime, default=dt.datetime.utcnow)
    status = Column(String, default="running")  # running | stopped | error
    config_json = Column(JSON)
    model_version = Column(String, default="gridbot-0.1.0")


class PaperFill(Base):
    __tablename__ = "paper_fills"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("paper_sessions.id"))
    timestamp = Column(DateTime, default=dt.datetime.utcnow)
    side = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    qty = Column(Float, nullable=False)
    slot_index = Column(Integer)
    pnl = Column(Float, nullable=True)


def get_engine(db_path: str = "sqlite:///gridbot.db"):
    engine = create_engine(db_path, future=True)
    Base.metadata.create_all(engine)
    return engine


def get_session(db_path: str = "sqlite:///gridbot.db"):
    engine = get_engine(db_path)
    Session = sessionmaker(bind=engine, future=True)
    return Session()
