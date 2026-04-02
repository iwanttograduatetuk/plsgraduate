"""
PostgreSQL ORM 모델 (SQLAlchemy 2.x)
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey,
    Integer, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Site(Base):
    __tablename__ = "sites"

    site_id:    Mapped[str]      = mapped_column(String, primary_key=True)
    site_name:  Mapped[str]      = mapped_column(String, nullable=False)
    location:   Mapped[Optional[str]] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    machines: Mapped[list["Machine"]] = relationship(back_populates="site")
    managers: Mapped[list["Manager"]] = relationship(back_populates="site")


class Machine(Base):
    __tablename__ = "machines"

    machine_id:   Mapped[str] = mapped_column(String, primary_key=True)
    site_id:      Mapped[str] = mapped_column(ForeignKey("sites.site_id"))
    machine_name: Mapped[Optional[str]] = mapped_column(String)
    # NORMAL / WARNING / CRITICAL
    status:       Mapped[str] = mapped_column(String, default="NORMAL")
    updated_at:   Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    site: Mapped["Site"] = relationship(back_populates="machines")
    events: Mapped[list["AnomalyEvent"]] = relationship(back_populates="machine")


class Manager(Base):
    __tablename__ = "managers"

    manager_id: Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id:    Mapped[Optional[str]] = mapped_column(ForeignKey("sites.site_id"), nullable=True)
    name:       Mapped[str]           = mapped_column(String)
    fcm_token:  Mapped[Optional[str]] = mapped_column(String)   # Android
    apns_token: Mapped[Optional[str]] = mapped_column(String)   # iOS

    site: Mapped[Optional["Site"]] = relationship(back_populates="managers")
    notifications: Mapped[list["NotificationHistory"]] = relationship(back_populates="manager")


class AnomalyEvent(Base):
    __tablename__ = "anomaly_events"

    event_id:             Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    site_id:              Mapped[str]  = mapped_column(String)
    machine_id:           Mapped[str]  = mapped_column(String, ForeignKey("machines.machine_id"))
    subsystem:            Mapped[str]  = mapped_column(String)   # coolant / hydraulics / probe
    detected_at:          Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reconstruction_error: Mapped[float]    = mapped_column(Float)
    anomaly_score:        Mapped[float]    = mapped_column(Float)
    severity:             Mapped[str]      = mapped_column(String)  # INFO / WARNING / CRITICAL
    feature_snapshot:     Mapped[dict]     = mapped_column(JSONB, default=dict)
    is_resolved:          Mapped[bool]     = mapped_column(Boolean, default=False)

    machine:   Mapped["Machine"]                      = relationship(back_populates="events")
    diagnosis: Mapped[Optional["FaultDiagnosisResult"]] = relationship(back_populates="event")
    notifications: Mapped[list["NotificationHistory"]]  = relationship(back_populates="event")


class FaultDiagnosisResult(Base):
    __tablename__ = "fault_diagnosis_results"

    result_id:   Mapped[UUID]     = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    event_id:    Mapped[UUID]     = mapped_column(ForeignKey("anomaly_events.event_id"), unique=True)
    model_name:  Mapped[str]      = mapped_column(String)   # CausReg / CausTR
    diagnosed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    root_causes: Mapped[list]     = mapped_column(JSONB, default=list)
    confidence:  Mapped[float]    = mapped_column(Float)

    event: Mapped["AnomalyEvent"] = relationship(back_populates="diagnosis")


class NotificationHistory(Base):
    __tablename__ = "notification_history"

    notif_id:   Mapped[UUID]     = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    event_id:   Mapped[UUID]     = mapped_column(ForeignKey("anomaly_events.event_id"))
    manager_id: Mapped[int]      = mapped_column(ForeignKey("managers.manager_id"))
    sent_at:    Mapped[datetime] = mapped_column(DateTime(timezone=True))
    channel:    Mapped[str]      = mapped_column(String)  # FCM / APNS / WEBSOCKET
    status:     Mapped[str]      = mapped_column(String)  # SENT / FAILED

    event:   Mapped["AnomalyEvent"] = relationship(back_populates="notifications")
    manager: Mapped["Manager"]      = relationship(back_populates="notifications")
