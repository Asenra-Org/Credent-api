import pytest
import uuid
import json
import redis
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
from app.queue.tasks import execute_with_boundary
from app.models.ase52 import AgentExecution, Job, Case, Tenant
from app.database.session import get_session_factory
from app.services.storage import StorageTimeoutError

class DummyTask:
    def __init__(self):
        self.request = MagicMock()
        self.request.id = "test-task-123"

    def retry(self, exc=None, countdown=None):
        raise RetryException(countdown=countdown, exc=exc)

class RetryException(Exception):
    def __init__(self, countdown, exc):
        self.countdown = countdown
        self.exc = exc

@pytest.fixture
def db_session():
    session_factory = get_session_factory()
    with session_factory() as session:
        yield session

@pytest.fixture
def setup_execution(db_session):
    tenant = db_session.query(Tenant).filter_by(id="test_tenant").first()
    if not tenant:
        tenant = Tenant(id="test_tenant", name="Test Tenant")
        db_session.add(tenant)
        db_session.commit()

    case = Case(id=f"CASE_{uuid.uuid4().hex}", tenant_id="test_tenant", borrower_name="Test", status="PROCESSING")
    job = Job(id=f"JOB_{uuid.uuid4().hex}", case_id=case.id, tenant_id="test_tenant", stage_name="stage_test", status="QUEUED")
    exec = AgentExecution(id=f"EXEC_{uuid.uuid4().hex}", job_id=job.id, tenant_id="test_tenant", agent_type="stage_test", attempt_number=1, status="PENDING")
    db_session.add(case)
    db_session.add(job)
    db_session.add(exec)
    db_session.commit()

    return {"execution_id": exec.id, "case_id": case.id}

def test_transient_retry_arithmetic(setup_execution, db_session):
    exec_id = setup_execution["execution_id"]

    @execute_with_boundary("stage_test")
    def fail_logic(self, data):
        raise StorageTimeoutError("Transient DB Lock")

    task = DummyTask()
    payload = json.dumps({"execution_id": exec_id})

    # Attempt 1
    with pytest.raises(RetryException) as exc_info:
        fail_logic(task, payload)
    assert exc_info.value.countdown == 60 # retry 1 (index 0) = 60s

    # Attempt 2
    with pytest.raises(RetryException) as exc_info:
        fail_logic(task, payload)
    assert exc_info.value.countdown == 120 # retry 2 (index 1) = 120s

    # Attempt 3
    with pytest.raises(RetryException) as exc_info:
        fail_logic(task, payload)
    assert exc_info.value.countdown == 240 # retry 3 (index 2) = 240s

    # Attempt 4 (Max limit reached)
    fail_logic(task, payload) # should not raise RetryException

    db_session.expire_all()
    execution = db_session.query(AgentExecution).filter_by(id=exec_id).first()
    assert execution.status == "FAILED"
    assert execution.attempt_number == 4

def test_permanent_failure_boundary(setup_execution, db_session):
    exec_id = setup_execution["execution_id"]

    @execute_with_boundary("stage_test")
    def fail_logic(self, data):
        raise ValueError("Corrupt PDF Parsing Failure")

    task = DummyTask()
    payload = json.dumps({"execution_id": exec_id})

    # Attempt 1 -> Immediate FAILED
    fail_logic(task, payload)

    db_session.expire_all()
    execution = db_session.query(AgentExecution).filter_by(id=exec_id).first()
    assert execution.status == "FAILED"
    assert execution.attempt_number == 1 # Did not increment



def test_corrupt_required_pdf(setup_execution, db_session):
    exec_id = setup_execution['execution_id']
    from app.queue.tasks import credent_ingest

    task = DummyTask()
    data = {
        'execution_id': exec_id,
        'tenant_id': 'test_tenant',
        'case_id': setup_execution['case_id'],
        'documents': [{'storage_key': 'fake_key', 'doc_role': 'REQUIRED'}]
    }

    with patch('app.queue.tasks.get_storage_service') as mock_storage, \
         patch('app.queue.tasks.DocumentIngestionAgent') as mock_agent:

        mock_storage.return_value.download_file.return_value = b'corrupt'

        async def mock_ingest_pdf(*args, **kwargs):
            return {'text': '', 'error': 'Corrupt PDF'}

        mock_agent.return_value.ingest_pdf = mock_ingest_pdf

        credent_ingest(json.dumps(data))

    db_session.expire_all()
    execution = db_session.query(AgentExecution).filter_by(id=exec_id).first()
    assert execution.status == 'FAILED'
    from app.models.ase52 import OutboxEvent
    outbox = db_session.query(OutboxEvent).filter_by(aggregate_id=setup_execution['case_id']).first()
    assert outbox is None

def test_corrupt_optional_pdf(setup_execution, db_session):
    exec_id = setup_execution['execution_id']
    from app.queue.tasks import credent_ingest

    task = DummyTask()
    data = {
        'execution_id': exec_id,
        'tenant_id': 'test_tenant',
        'case_id': setup_execution['case_id'],
        'documents': [{'storage_key': 'fake_key', 'doc_role': 'OPTIONAL'}]
    }

    with patch('app.queue.tasks.get_storage_service') as mock_storage, \
         patch('app.queue.tasks.DocumentIngestionAgent') as mock_agent:

        mock_storage.return_value.download_file.return_value = b'corrupt'

        async def mock_ingest_pdf(*args, **kwargs):
            return {'text': '', 'error': 'Corrupt PDF'}

        mock_agent.return_value.ingest_pdf = mock_ingest_pdf

        async def mock_parse(*args, **kwargs):
            return {'fake': 'data'}
        mock_agent.return_value.parse_financial_statement = mock_parse

        credent_ingest(json.dumps(data))

    db_session.expire_all()
    execution = db_session.query(AgentExecution).filter_by(id=exec_id).first()
    assert execution.status == 'SUCCESS'
    from app.models.ase52 import OutboxEvent
    outbox = db_session.query(OutboxEvent).filter_by(aggregate_id=setup_execution['case_id']).first()
    assert outbox is not None
    payload = json.loads(outbox.payload)
    assert 'audit_warnings' in payload
    assert len(payload['audit_warnings']) == 1
    assert payload['audit_warnings'][0]['role'] == 'OPTIONAL'

def test_final_cam_propagation(setup_execution, db_session):
    exec_id = setup_execution['execution_id']
    from app.queue.tasks import credent_synthesis

    task = DummyTask()
    data = {
        'execution_id': exec_id,
        'tenant_id': 'test_tenant',
        'case_id': setup_execution['case_id'],
        'audit_warnings': [{'document': 'gst_returns', 'role': 'OPTIONAL', 'reason': 'Parsing Failure: Corrupt PDF'}]
    }

    with patch('app.queue.tasks.get_storage_service') as mock_storage:

        # intercept upload_file to check content
        uploaded_content = []
        def upload_mock(tenant_id, case_id, document_id, filename, content, content_type):
            uploaded_content.append(content)
            return 'fake_cam_key'

        mock_storage.return_value.upload_file = upload_mock

        credent_synthesis(json.dumps(data))

        assert len(uploaded_content) == 1
        cam_json = json.loads(uploaded_content[0].decode())
        assert 'audit_warnings' in cam_json
        assert cam_json['audit_warnings'][0]['role'] == 'OPTIONAL'
