from datetime import datetime
from sqlalchemy import String,ForeignKey,UniqueConstraint,DateTime,JSON,Double,Integer
from sqlalchemy.orm import Mapped,mapped_column
from src.config.database import Base

class SensitivityDiagnostic(Base):
    """One leave-one-out diagnostic per frozen alignment study.

    Kept in its own table so the published run result is never overwritten.
    ``run_id`` is unique: repeating the request returns the existing diagnosis
    instead of appending a new one.
    """
    __tablename__='sensitivity_diagnostics'
    id:Mapped[int]=mapped_column(primary_key=True)
    run_id:Mapped[int]=mapped_column(ForeignKey('alignment_runs.id'),unique=True)
    status:Mapped[str]=mapped_column(String(20),default='queued')
    basis_fingerprint:Mapped[str]=mapped_column(String(64),default='')
    total:Mapped[int]=mapped_column(Integer,default=0)
    retained:Mapped[int]=mapped_column(Integer,default=1)
    effective_retained:Mapped[int]=mapped_column(Integer,default=0)
    tolerance:Mapped[float]=mapped_column(Double,default=1e-12)
    summary:Mapped[dict]=mapped_column(JSON,default=dict)
    error:Mapped[str]=mapped_column(String(600),default='')
    created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
    updated_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)

class SensitivityItem(Base):
    """Persisted per-specimen leave-one-out fit, reused when a run is resumed."""
    __tablename__='sensitivity_items'
    id:Mapped[int]=mapped_column(primary_key=True)
    diagnostic_id:Mapped[int]=mapped_column(ForeignKey('sensitivity_diagnostics.id'))
    specimen_id:Mapped[int]=mapped_column(ForeignKey('shape_specimens.id'))
    code:Mapped[str]=mapped_column(String(50))
    name:Mapped[str]=mapped_column(String(100),default='')
    status:Mapped[str]=mapped_column(String(20))
    reason:Mapped[str]=mapped_column(String(600),default='')
    metrics:Mapped[dict]=mapped_column(JSON,default=dict)
    created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
    __table_args__=(UniqueConstraint('diagnostic_id','specimen_id'),)
