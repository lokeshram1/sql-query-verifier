import json
import sys
import types

from fastapi.testclient import TestClient

from app import BAD_SQL, GOOD_SQL, app, run, verify

client = TestClient(app)

def test_demo_detects_real_inflation_and_reference_passes():
    result = client.post('/api/demo').json()
    assert result['flawed']['report']['status'] == 'FAIL'
    assert result['corrected']['report']['status'] == 'PASS'
    bad_total = sum(r['revenue'] for r in result['flawed']['rows'])
    good_total = sum(r['revenue'] for r in result['corrected']['rows'])
    assert bad_total > good_total
    assert any(c['name'] == 'Join fan-out and grain' and c['status'] == 'FAIL' for c in result['flawed']['report']['checks'])

def test_read_only_select_and_with():
    assert run('SELECT COUNT(*) AS n FROM orders')[0][0]['n'] == 7
    assert run('WITH x AS (SELECT 1 n) SELECT n FROM x')[0][0]['n'] == 1

def test_writes_and_multiple_statements_rejected():
    for sql in ['INSERT INTO customers VALUES(9,\'x\',\'x\',NULL)', 'UPDATE customers SET name=\'x\'', 'DELETE FROM orders', 'DROP TABLE orders', 'SELECT 1; SELECT 2', 'PRAGMA table_info(orders)', 'ATTACH DATABASE \'x\' AS x']:
        response = client.post('/api/run', json={'sql': sql})
        assert response.status_code == 400, sql

def test_unknown_table_and_column_are_safe_errors():
    for sql in ['SELECT * FROM missing_table', 'SELECT nonexistent FROM customers']:
        response = client.post('/api/run', json={'sql': sql})
        assert response.status_code == 400
        assert 'Traceback' not in response.text

def test_sql_authorizer_restricts_reads_and_unsafe_functions():
    for sql in ["SELECT name FROM sqlite_master", "SELECT load_extension('missing')", "SELECT readfile('outside')"]:
        response = client.post('/api/run', json={'sql': sql})
        assert response.status_code == 400

def test_empty_and_null_checks():
    rows, _ = run('SELECT email FROM customers WHERE 0')
    report = verify('SELECT email FROM customers WHERE 0', rows)
    assert any(c['name']=='Result shape' and c['status']=='WARNING' for c in report['checks'])
    rows, _ = run('SELECT NULL AS important')
    report = verify('SELECT NULL AS important', rows)
    assert any(c['name']=='Important output nulls' and c['status']=='WARNING' for c in report['checks'])

def test_duplicate_key_and_fanout_fixture_is_detected():
    rows, _=run(BAD_SQL)
    report=verify(BAD_SQL, rows)
    assert report['status']=='FAIL'
    assert any(c['name']=='Seeded revenue reconciliation' and c['status']=='FAIL' for c in report['checks'])

def test_mock_generation_and_api_workflow():
    generated=client.post('/api/generate',json={'question':'Revenue by segment'}).json()
    assert generated['sql']==GOOD_SQL
    result=client.post('/api/run',json={'question':'Revenue by segment','sql':generated['sql']})
    assert result.status_code==200 and result.json()['report']['status']=='PASS'

def test_csv_and_json_export():
    rows=client.post('/api/run',json={'sql':GOOD_SQL}).json()['rows']
    csv=client.post('/api/export/csv',json={'rows':rows})
    assert csv.status_code==200 and 'segment,revenue' in csv.text
    report=client.post('/api/export/json',json={'sql':GOOD_SQL,'report':{'status':'PASS'}})
    assert report.status_code==200 and json.loads(report.text)['report']['status']=='PASS'

def test_numeric_baseline_tolerance():
    correct,_=run(GOOD_SQL)
    near=[dict(r) for r in correct]
    near[0]['revenue'] += 0.001
    report=verify(GOOD_SQL,near)
    assert next(c for c in report['checks'] if c['name']=='Seeded revenue reconciliation')['status']=='PASS'

def test_duplicate_segment_grain_fails_reconciliation():
    rows, _ = run("SELECT 'SMB' AS segment, 480 AS revenue UNION ALL SELECT 'SMB', 480")
    report = verify("SELECT 'SMB' AS segment, 480 AS revenue UNION ALL SELECT 'SMB', 480", rows)
    assert next(c for c in report['checks'] if c['name']=='Seeded revenue reconciliation')['status']=='FAIL'

def test_openai_provider_rejects_malformed_structured_metadata(monkeypatch):
    class Completion:
        def create(self, **kwargs):
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=json.dumps({"sql":"SELECT 1", "assumptions":"should be a list"})))])
    class FakeOpenAI:
        def __init__(self): self.chat=types.SimpleNamespace(completions=Completion())
    monkeypatch.setenv('LLM_PROVIDER','openai')
    monkeypatch.setenv('OPENAI_API_KEY','test-key')
    monkeypatch.setitem(sys.modules,'openai',types.SimpleNamespace(OpenAI=FakeOpenAI))
    from fastapi import HTTPException
    import pytest
    with pytest.raises(HTTPException) as exc:
        from app import generation
        generation('Revenue by segment')
    assert exc.value.status_code == 502
    assert 'ValueError' in exc.value.detail
