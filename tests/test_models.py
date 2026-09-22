import asyncio,json,os,sys
from pathlib import Path
import pytest,httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import core as C
import intelligence as AI
import app as A
from fastapi.testclient import TestClient

@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(C,'DATA',tmp_path/'data');monkeypatch.setattr(C,'BASE',Path(__file__).resolve().parents[1]);C.init()
    monkeypatch.delenv('ANTHROPIC_API_KEY',raising=False);monkeypatch.delenv('OPENAI_API_KEY',raising=False)

def configure(**updates):
    s=C.settings();s.update(updates);C.save_json(C.DATA/'settings.json',s)

def test_legacy_settings_migrate_without_losing_models():
    C.save_json(C.DATA/'settings.json',{'provider':'anthropic','score_model':'claude-example','email_model':'','search_model':'claude-search','max_web_searches':5})
    assert AI.selection('score')==('anthropic','claude-example')
    assert AI.selection('email')==('anthropic','claude-example')

def test_mixed_providers_and_task_availability(monkeypatch):
    configure(score_provider='openai',score_model='gpt-example',search_provider='anthropic',search_model='claude-example',email_provider='none')
    monkeypatch.setenv('OPENAI_API_KEY','test-openai')
    assert AI.available() and not AI.available('search') and not AI.available('email')
    monkeypatch.setenv('ANTHROPIC_API_KEY','test-anthropic');assert AI.available('search')

def test_openai_structured_request_and_validation(monkeypatch):
    async def fake(payload,provider):
        assert provider=='openai' and payload['model']=='gpt-example' and payload['store'] is False
        assert payload['tool_choice']=={'type':'function','name':'record_results'}
        schema=payload['tools'][0]['parameters']
        assert schema['additionalProperties'] is False
        assert schema['$defs']['Score']['additionalProperties'] is False
        return {'output':[{'type':'function_call','name':'record_results','arguments':json.dumps({'results':[{'i':0,'score':88,'work_mode':'remote','exclude':False,'rationale':'Good fit'}]})}]}
    monkeypatch.setattr(AI,'request',fake)
    result=asyncio.run(AI.structured(AI.Scores,'system','jobs','gpt-example','openai'))
    assert result.results[0].score==88

def test_anthropic_still_routes_correctly(monkeypatch):
    async def fake(payload,provider):
        assert provider=='anthropic' and payload['model']=='claude-example'
        assert payload['tools'][0]['input_schema']['additionalProperties'] is False
        return {'content':[{'type':'tool_use','name':'record_results','input':{'company':'Org','role_title':'CTO','outcome':'screening','summary':'Call requested'}}]}
    monkeypatch.setattr(AI,'request',fake)
    assert asyncio.run(AI.structured(AI.EmailResult,'system','email','claude-example')).outcome=='screening'

def test_invalid_openai_output_rejected(monkeypatch):
    async def fake(*args):return {'output':[{'type':'function_call','name':'record_results','arguments':'{"results":[{"score":900}]}'}]}
    monkeypatch.setattr(AI,'request',fake)
    with pytest.raises(ValueError):asyncio.run(AI.structured(AI.Scores,'','','gpt-example','openai'))

def test_model_lists_without_keys():
    assert asyncio.run(AI.list_models('openai'))['configured'] is False

class FakeClient:
    responses=[];requests=[]
    def __init__(self,**kwargs):pass
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def get(self,url,**kwargs):
        self.requests.append((url,kwargs));return self.responses.pop(0)
    async def post(self,url,**kwargs):
        self.requests.append((url,kwargs));return self.responses.pop(0)

def mock_http(monkeypatch,responses):
    FakeClient.responses=responses;FakeClient.requests=[];monkeypatch.setattr(AI.httpx,'AsyncClient',FakeClient)

def test_anthropic_model_pagination(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY','anthropic-key')
    mock_http(monkeypatch,[httpx.Response(200,json={'data':[{'id':'claude-a','display_name':'Claude A'}],'has_more':True,'last_id':'claude-a'}),httpx.Response(200,json={'data':[{'id':'claude-b'}],'has_more':False})])
    result=asyncio.run(AI.list_models('anthropic'))
    assert len(result['models'])==2 and result['error'] is None
    assert FakeClient.requests[1][1]['params']['after_id']=='claude-a'

def test_openai_list_filters_non_text_models(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','openai-key')
    mock_http(monkeypatch,[httpx.Response(200,json={'data':[{'id':m} for m in ['gpt-6-example','o3','gpt-image-1','text-embedding-3-small','gpt-4o-realtime-preview']]})])
    result=asyncio.run(AI.list_models('openai'))
    assert {m['id'] for m in result['models']}=={'gpt-6-example','o3'}
    assert FakeClient.requests[0][1]['headers']['Authorization']=='Bearer openai-key'

def test_provider_error_does_not_expose_key(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','secret-value')
    mock_http(monkeypatch,[httpx.Response(401,json={'error':{'message':'Invalid key secret-value'}})])
    result=asyncio.run(AI.list_models('openai'))
    assert '401' in result['error'] and 'secret-value' not in result['error']

def test_openai_quota_stops_without_retry(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','test')
    mock_http(monkeypatch,[httpx.Response(429,json={'error':{'code':'insufficient_quota','message':'Insufficient quota'}})])
    with pytest.raises(AI.BillingError):asyncio.run(AI.request({'model':'gpt-example'},'openai'))
    assert len(FakeClient.requests)==1

def test_openai_request_endpoint_and_incomplete(monkeypatch):
    mock_http(monkeypatch,[httpx.Response(200,json={'status':'incomplete'})])
    with pytest.raises(ValueError):asyncio.run(AI.request({'model':'gpt-example'},'openai'))
    assert FakeClient.requests[0][0]=='https://api.openai.com/v1/responses'

def test_openai_deep_search_accepts_only_observed_urls(monkeypatch):
    configure(search_provider='openai',search_model='gpt-example')
    monkeypatch.setenv('OPENAI_API_KEY','test')
    async def fake_request(payload,provider):
        assert provider=='openai' and payload['max_tool_calls']==5
        assert payload['include']==['web_search_call.action.sources']
        return {'output':[{'type':'web_search_call','action':{'query':'CTO nonprofit','sources':[{'url':'https://example.org/real'}]}}]}
    async def fake_structured(*args):
        assert args[-1]=='openai'
        return AI.FoundList(listings=[AI.Found(title='CTO',company='Org',location='United States',url='https://example.org/'+tail,summary='Lead the team',work_mode='remote') for tail in ['real','invented']])
    async def fake_verify(j):return 'open','Verified'
    monkeypatch.setattr(AI,'request',fake_request);monkeypatch.setattr(AI,'structured',fake_structured);monkeypatch.setattr(AI,'verify',fake_verify)
    result=asyncio.run(AI.deep_search(C.profile(),'',lambda *a:None));assert len(result)==1 and result[0]['url'].endswith('/real')

def test_save_mixed_settings_and_separate_secrets(monkeypatch,tmp_path):
    # Seed the profile before redirecting the .env write location.
    monkeypatch.setattr(C,'BASE',tmp_path)
    with TestClient(A.app) as c:
        h={'x-local-token':A.TOKEN}
        r=c.put('/api/keys',headers=h,json={'anthropic_api_key':'claude-secret','openai_api_key':'openai-secret'});assert r.status_code==200
        body={'provider':'openai','score_provider':'openai','score_model':'gpt-example','email_provider':'anthropic','email_model':'claude-example','search_provider':'openai','search_model':'gpt-search','max_web_searches':4}
        assert c.put('/api/settings',headers=h,json=body).status_code==200
        saved=C.settings();assert saved['email_provider']=='anthropic'
        assert 'secret' not in (C.DATA/'settings.json').read_text()
        assert (tmp_path/'.env').stat().st_mode & 0o777==0o600
        state=c.get('/api/state').json();assert state['provider_keys']=={'anthropic':True,'openai':True}
        assert 'openai-secret' not in json.dumps(state)
    monkeypatch.delenv('ANTHROPIC_API_KEY',raising=False);monkeypatch.delenv('OPENAI_API_KEY',raising=False)

def test_provider_failure_does_not_hide_other_catalog(monkeypatch):
    async def fake(provider):return {'provider':provider,'configured':True,'models':[] if provider=='anthropic' else [{'id':'gpt-example','provider':'openai','name':'GPT'}],'error':'Invalid key' if provider=='anthropic' else None}
    monkeypatch.setattr(AI,'list_models',fake)
    with TestClient(A.app) as c:
        result=c.get('/api/models').json();assert len(result['models'])==1 and result['providers']['anthropic']['error']
