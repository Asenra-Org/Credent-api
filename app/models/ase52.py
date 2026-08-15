from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    String, Integer, Text, DateTime, ForeignKey, 
    UniqueConstraint, Index, CheckConstraint, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

class Tenant(Base):
    __tablename__ = 'tenants'
    
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default='ACTIVE')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    cases: Mapped[List["Case"]] = relationship("Case", back_populates="tenant")

class Case(Base):
    __tablename__ = 'cases'
    
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False)
    borrower_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="cases")
    documents: Mapped[List["Document"]] = relationship("Document", back_populates="case", cascade="all, delete-orphan")
    jobs: Mapped[List["Job"]] = relationship("Job", back_populates="case", cascade="all, delete-orphan")
    appraisal_results: Mapped[List["AppraisalResult"]] = relationship("AppraisalResult", back_populates="case", cascade="all, delete-orphan")
    
    __table_args__ = (
        CheckConstraint("status IN ('INITIATED', 'QUEUED', 'PROCESSING', 'PAUSED', 'COMPLETED', 'FAILED')", name='chk_case_status'),
        Index('idx_cases_tenant_status', 'tenant_id', 'status'),
        Index('idx_cases_tenant_created', 'tenant_id', 'created_at'),
    )

class Document(Base):
    __tablename__ = 'documents'
    
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(String, ForeignKey('cases.id', ondelete='CASCADE'), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    doc_role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    case: Mapped["Case"] = relationship("Case", back_populates="documents")
    
    __table_args__ = (
        CheckConstraint("doc_role IN ('REQUIRED', 'OPTIONAL')", name='chk_doc_role'),
        CheckConstraint("status IN ('UPLOAD_INTENT', 'UPLOADING', 'UPLOAD_COMPLETE', 'DB_REGISTERED', 'ACTIVE', 'RETENTION_LOCKED', 'DELETABLE', 'DELETED')", name='chk_doc_status'),
        Index('idx_documents_case_id', 'case_id'),
        Index('idx_documents_tenant_status', 'tenant_id', 'status'),
    )

class Job(Base):
    __tablename__ = 'jobs'
    
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(String, ForeignKey('cases.id', ondelete='CASCADE'), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False)
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(128))
    release_tag: Mapped[Optional[str]] = mapped_column(String(128))
    stage_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    case: Mapped["Case"] = relationship("Case", back_populates="jobs")
    executions: Mapped[List["AgentExecution"]] = relationship("AgentExecution", back_populates="job", cascade="all, delete-orphan")
    
    __table_args__ = (
        CheckConstraint("status IN ('QUEUED', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED', 'SKIPPED')", name='chk_job_status'),
        Index('idx_jobs_case_id', 'case_id'),
        Index('idx_jobs_tenant_release', 'tenant_id', 'release_tag'),
        Index('idx_jobs_celery_task', 'celery_task_id'),
    )

class AgentExecution(Base):
    __tablename__ = 'agent_executions'
    
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(String, ForeignKey('jobs.id', ondelete='CASCADE'), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False)
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(128))
    release_tag: Mapped[Optional[str]] = mapped_column(String(128))
    agent_type: Mapped[str] = mapped_column(String(128), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_hash: Mapped[Optional[str]] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    output_storage_key: Mapped[Optional[str]] = mapped_column(String(512))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    job: Mapped["Job"] = relationship("Job", back_populates="executions")
    
    __table_args__ = (
        CheckConstraint("attempt_number >= 1 AND attempt_number <= 4", name='chk_agent_exec_attempt'),
        CheckConstraint("status IN ('PENDING', 'RUNNING', 'UNKNOWN_EXTERNAL_OUTCOME', 'RESULT_RECOVERABLE', 'RETRY_AUTHORIZED', 'SUCCESS', 'FAILED', 'TIMED_OUT', 'SKIPPED')", name='chk_agent_exec_status'),
        Index('idx_agent_exec_job_id', 'job_id'),
        Index('idx_agent_exec_tenant_release', 'tenant_id', 'release_tag'),
    )

class AppraisalResult(Base):
    __tablename__ = 'appraisal_results'
    
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(String, ForeignKey('cases.id', ondelete='CASCADE'), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False)
    base_score: Mapped[Optional[int]] = mapped_column(Integer)
    adjusted_score: Mapped[Optional[int]] = mapped_column(Integer)
    decision: Mapped[Optional[str]] = mapped_column(String(32))
    cam_report_storage_key: Mapped[Optional[str]] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    case: Mapped["Case"] = relationship("Case", back_populates="appraisal_results")
    
    __table_args__ = (
        Index('idx_appraisal_results_case_id', 'case_id'),
        Index('idx_appraisal_results_tenant_id', 'tenant_id'),
    )

class IdempotencyRecord(Base):
    __tablename__ = 'idempotency_records'
    
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    response_payload: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    __table_args__ = (
        UniqueConstraint('tenant_id', 'idempotency_key', name='uq_idempotency_tenant_key'),
        Index('idx_idempotency_tenant_key', 'tenant_id', 'idempotency_key'),
    )

class OutboxEvent(Base):
    __tablename__ = 'outbox_events'
    
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default='PENDING', nullable=False)
    deduplication_key: Mapped[Optional[str]] = mapped_column(String(128))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    release_tag: Mapped[Optional[str]] = mapped_column(String(128))
    
    __table_args__ = (
        CheckConstraint("status IN ('PENDING', 'CLAIMED', 'PUBLISHED', 'FAILED', 'DEAD_LETTERED')", name='chk_outbox_status'),
        UniqueConstraint('tenant_id', 'deduplication_key', name='uq_outbox_dedup'),
        Index('idx_outbox_tenant_aggregate', 'tenant_id', 'aggregate_type', 'aggregate_id'),
    )
