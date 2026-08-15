import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.ase52 import OutboxEvent

logger = logging.getLogger(__name__)

# =============================================================================
# EXCEPTIONS
# =============================================================================

class TransientTransportError(Exception):
    pass

class PermanentPayloadError(Exception):
    """Raised on unrecoverable event errors (e.g. malformed JSON, release tag mismatch, missing tenant)."""
    pass

# =============================================================================
# PAYLOAD
# =============================================================================

class OutboxPayload(BaseModel):
    """Validated, sanitized payload structure for outbox event dispatch."""
    event_id: str
    tenant_id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    release_tag: str
    data: Dict[str, Any]

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw_json: str) -> 'OutboxPayload':
        try:
            return cls.model_validate(json.loads(raw_json))
        except Exception as e:
            raise PermanentPayloadError(f"Failed to parse outbox payload JSON: {e}")

# =============================================================================
# TRANSPORT ADAPTERS
# =============================================================================

class MessageBrokerTransport:
    """Abstract interface for outbox event message broker transport adapters."""
    def publish(self, queue_name: str, task_name: str, payload_json: str, tenant_id: str) -> str:
        raise NotImplementedError("Publishes serialized event JSON to the message queue, returning dispatch message/task ID.")

class InMemoryTransportAdapter(MessageBrokerTransport):
    """In-memory transport adapter designed for unit testing, offline execution, and local verification."""
    def __init__(self):
        self.queues = {}
        
    def publish(self, queue_name: str, task_name: str, payload_json: str, tenant_id: str) -> str:
        if queue_name not in self.queues:
            self.queues[queue_name] = []
        self.queues[queue_name].append(payload_json)
        return "msg_123"
        
    def get_published_messages(self, queue_name: str) -> List[str]:
        return self.queues.get(queue_name, [])
        
    def clear(self):
        self.queues.clear()

class CeleryTransportAdapter(MessageBrokerTransport):
    """Production Celery transport adapter publishing outbox payloads directly via Celery protocol."""
    def __init__(self, celery_app):
        self.app = celery_app
        
    def publish(self, queue_name: str, task_name: str, payload_json: str, tenant_id: str) -> str:
        try:
            res = self.app.send_task(
                task_name,
                args=[],
                kwargs={"payload": payload_json},
                queue=queue_name
            )
            return res.id
        except Exception as e:
            raise TransientTransportError(f"Failed to publish to Celery: {e}")

# =============================================================================
# DISPATCHER
# =============================================================================

class OutboxDispatcher:
    """Durable Transactional Outbox Dispatcher Engine enforcing at-least-once transport delivery."""
    def __init__(self, session_factory, transport: MessageBrokerTransport, batch_size: int = 50, lease_seconds: int = 300, max_attempts: int = 4):
        self.session_factory = session_factory
        self.transport = transport
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts

    def get_release_tag(self) -> str:
        return "release_1"

    def reconcile_expired_leases(self) -> int:
        """Releases abandoned CLAIMED events whose lease has expired back to PENDING."""
        with self.session_factory() as session:
            now = datetime.now(timezone.utc)
            stmt = select(OutboxEvent).where(
                OutboxEvent.status == 'CLAIMED',
                OutboxEvent.lease_until < now
            ).with_for_update(skip_locked=True)
            
            events = session.scalars(stmt).all()
            count = 0
            for event in events:
                event.status = 'PENDING'
                event.lease_until = None
                count += 1
                
            session.commit()
            if count > 0:
                logger.info(f"Reconciled and recovered {count} expired outbox event lease(s).")
            return count

    def dispatch_batch(self) -> int:
        """Claims a batch of PENDING outbox events and publishes them to transport outside DB row locks."""
        claimed_events = []
        
        # 1. Claim Batch in Transaction
        with self.session_factory() as session:
            now = datetime.now(timezone.utc)
            
            # Using skip_locked to avoid blocking on concurrent dispatchers
            stmt = select(OutboxEvent).where(
                OutboxEvent.status == 'PENDING',
                OutboxEvent.available_at <= now
            ).limit(self.batch_size).with_for_update(skip_locked=True)
            
            events = session.scalars(stmt).all()
            
            for event in events:
                event.status = 'CLAIMED'
                event.lease_until = now + timedelta(seconds=self.lease_seconds)
                # Store lightweight representation in memory for transport phase
                claimed_events.append({
                    "id": event.id,
                    "tenant_id": event.tenant_id,
                    "aggregate_type": event.aggregate_type,
                    "aggregate_id": event.aggregate_id,
                    "event_type": event.event_type,
                    "payload": event.payload,
                    "release_tag": event.release_tag,
                    "attempt_count": event.attempt_count
                })
                
            session.commit()
            
        if not claimed_events:
            return 0
            
        success_count = 0
        
        # 2. Publish outside transaction
        for event_data in claimed_events:
            event_id = event_data['id']
            try:
                # Validate payload
                if event_data['release_tag'] != self.get_release_tag():
                    raise PermanentPayloadError(f"Release tag mismatch: event has '{event_data['release_tag']}', server running '{self.get_release_tag()}'.")
                
                try:
                    data = json.loads(event_data['payload'])
                except Exception as e:
                    raise PermanentPayloadError(f"Invalid payload JSON formatting: {e}")
                    
                payload = OutboxPayload(
                    event_id=event_id,
                    tenant_id=event_data['tenant_id'],
                    aggregate_type=event_data['aggregate_type'],
                    aggregate_id=event_data['aggregate_id'],
                    event_type=event_data['event_type'],
                    release_tag=event_data['release_tag'],
                    data=data
                )
                
                # Routing
                queue_name = "credent_default"
                task_name = "app.queue.tasks.ping"
                
                if payload.event_type == "CASE_CREATED":
                    queue_name = "credent_ingest"
                    task_name = "app.queue.tasks.stage_1_ingest"
                elif payload.event_type == "STAGE_2_COMPLETED":
                    queue_name = "credent_synthesis"
                    task_name = "app.queue.tasks.stage_3_synthesis_chord"
                elif payload.event_type == "STAGE_1_COMPLETED":
                    queue_name = "credent_analysis"
                    task_name = "app.queue.tasks.stage_2_analysis_group"
                    
                # Publish
                self.transport.publish(queue_name, task_name, payload.to_json(), payload.tenant_id)
                
                # 3a. Mark Published
                with self.session_factory() as session:
                    ev = session.get(OutboxEvent, event_id)
                    if ev:
                        ev.status = 'PUBLISHED'
                        ev.published_at = datetime.now(timezone.utc)
                        ev.lease_until = None
                        session.commit()
                
                success_count += 1
                logger.info(f"Outbox event '{event_id}' for tenant '{payload.tenant_id}' successfully published.")
                
            except TransientTransportError as e:
                logger.warning(f"Transient transport failure for outbox event '{event_id}': {e}")
                self._record_failure(event_id, str(e), transient=True)
            except PermanentPayloadError as e:
                logger.error(f"Permanent payload error for outbox event '{event_id}': {e}")
                self._record_failure(event_id, str(e), transient=False)
            except Exception as e:
                logger.error(f"Unexpected error processing outbox event '{event_id}': Unexpected error: {e}")
                self._record_failure(event_id, str(e), transient=False)
                
        return success_count

    def _record_failure(self, event_id: str, error_msg: str, transient: bool):
        with self.session_factory() as session:
            ev = session.get(OutboxEvent, event_id)
            if not ev:
                return
            
            ev.attempt_count += 1
            ev.last_error = error_msg
            ev.lease_until = None
            
            if ev.attempt_count >= self.max_attempts or not transient:
                ev.status = 'DEAD_LETTERED'
            else:
                ev.status = 'FAILED'
                # Exponential backoff
                ev.available_at = datetime.now(timezone.utc) + timedelta(seconds=2 ** ev.attempt_count)
                
            session.commit()
