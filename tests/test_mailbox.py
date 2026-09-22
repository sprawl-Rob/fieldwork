import asyncio,json,sys,time
from pathlib import Path
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import core as C
import ms_mailbox as M
import app as A

@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(C,'DATA',tmp_path/'data');C.init();M.init()
    M.session.clear();M.pending.clear();M.scan_task=None
    yield
    M.session.clear();M.pending.clear();M.scan_task=None

def seed(eid='evidence',date='2026-04-01T10:00:00Z'):
    with C.db() as c:c.execute('INSERT INTO mailbox_evidence(id,account,subject,sender,received,body,decision) VALUES(?,?,?,?,?,?,?)',(eid,'a','Your application','hr@example.org',date,'We received your application.','pending'))

def test_oauth_pkce_scope_and_no_secret():
    r=M.connect(M.Connection(tenant_id='11111111-1111-1111-1111-111111111111',client_id='22222222-2222-2222-2222-222222222222'))
    from urllib.parse import urlsplit,parse_qs
    q=parse_qs(urlsplit(r['url']).query)
    assert q['code_challenge_method']==['S256']
    assert q['redirect_uri']==[M.REDIRECT]
    assert 'Mail.ReadWrite' not in q['scope'][0] and 'Mail.Send' not in q['scope'][0]
    assert 'client_secret' not in q and 'access_token' not in M.state()
    assert len(M.pending['verifier'])>=43

def test_callback_rejects_wrong_and_expired_state():
    M.pending.update(state='expected',expires=time.time()+60,verifier='secret')
    with pytest.raises(HTTPException):asyncio.run(M.callback(state='wrong',code='x'))
    M.pending['expires']=0
    with pytest.raises(HTTPException):asyncio.run(M.callback(state='expected',code='x'))

def test_callback_consumes_state_and_establishes_identity(monkeypatch):
    M.pending.update(state='expected',expires=time.time()+60,verifier='secret')
    async def token(fields):
        assert fields['code_verifier']=='secret'
        return {'access_token':'private','expires_in':3600}
    async def graph(url,params=None):return {'id':'user','mail':'me@example.org'}
    monkeypatch.setattr(M,'token_request',token);monkeypatch.setattr(M,'graph',graph)
    monkeypatch.setattr(M,'config',lambda:{'tenant_id':'tenant'})
    assert 'connected' in asyncio.run(M.callback(state='expected',code='code'))
    assert M.session['account']=='tenant:user' and not M.pending
    assert 'private' not in json.dumps(M.state())

def test_pagination_refuses_external_credentials():
    M.session.update(access_token='secret',expires=time.time()+600)
    with pytest.raises(ValueError,match='unexpected'):asyncio.run(M.graph('https://evil.example/v1.0/next'))

def test_nested_scan_pagination_and_dedupe(monkeypatch):
    M.session['account']='account'
    msg={'id':'id1','internetMessageId':'same-message','subject':'Application received','receivedDateTime':'2026-06-01T12:00:00Z','body':{'content':'Thanks for applying','contentType':'text'},'from':{'emailAddress':{'address':'hr@example.org'}}}
    calls=[]
    async def graph(url,params=None):
        calls.append(url)
        if url=='/me/mailFolders/inbox':return {'id':'inbox','displayName':'Inbox'}
        if url=='/me/mailFolders/inbox/messages':
            assert 'receivedDateTime ge ' in params['$filter']
            return {'value':[msg],'@odata.nextLink':M.GRAPH+'/next'}
        if url==M.GRAPH+'/next':return {'value':[]}
        if url=='/me/mailFolders/inbox/childFolders':return {'value':[{'id':'child','displayName':'Jobs'}]}
        if url=='/me/mailFolders/child/messages':return {'value':[dict(msg,id='moved')]}
        if url=='/me/mailFolders/child/childFolders':return {'value':[]}
        raise AssertionError(url)
    monkeypatch.setattr(M,'graph',graph)
    asyncio.run(M.scan());asyncio.run(M.scan())
    assert M.progress['state']=='complete' and M.progress['folders']==2
    assert M.evidence()['total']==1 and M.GRAPH+'/next' in calls

def test_local_filter_html_and_known_company():
    assert M.clean_body({'body':{'content':'<p>Received <b>application</b></p>','contentType':'html'}})=='Received application'
    assert not M.candidate('An update','Example Organization thanks you',['Example Organization'])
    assert M.candidate('Next steps','Please schedule an interview',[])
    assert not M.candidate('Groceries','Buy milk',[])

def test_scan_failure_keeps_partial_queue(monkeypatch):
    seed();M.session['account']='a'
    async def broken(*args,**kwargs):raise ValueError('Access denied')
    monkeypatch.setattr(M,'graph',broken);asyncio.run(M.scan())
    assert M.progress['state']=='failed' and M.evidence()['total']==1

def test_classification_only_explicit_and_cached(monkeypatch):
    seed();calls=[]
    async def classify(text):
        calls.append(text)
        return A.AI.EmailResult(company='Example',role_title='CTO',outcome='acknowledged',summary='Application received')
    monkeypatch.setattr(M.AI,'classify',classify)
    asyncio.run(M.classify('evidence'));asyncio.run(M.classify('evidence'))
    assert len(calls)==1 and 'From: hr@example.org' in calls[0]

def test_historical_approval_updates_new_job_and_is_idempotent():
    seed();jid,_=C.upsert({'company':'Example','title':'CTO'})
    assert M.approve('evidence',M.Approval(job_id=jid,status='acknowledged'))['message']=='Updated'
    with C.db() as c:assert c.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()[0]=='acknowledged'
    with pytest.raises(HTTPException):M.approve('evidence',M.Approval(job_id=jid,status='rejected'))

def test_older_email_does_not_overwrite_newer_stage():
    seed();jid,_=C.upsert({'company':'Example','title':'CTO'})
    C.update_status(jid,'interviewing',occurred_at='2026-08-01T10:00:00Z',force=True)
    result=M.approve('evidence',M.Approval(job_id=jid,status='acknowledged'))
    assert 'protected' in result['message']
    with C.db() as c:assert c.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()[0]=='interviewing'

def test_create_missing_and_dismiss():
    seed();M.approve('evidence',M.Approval(create=True,company='New Org',title='CTO',status='rejected'))
    with C.db() as c:assert c.execute('SELECT status FROM jobs').fetchone()[0]=='rejected'
    seed('other');M.dismiss('other');assert M.evidence()['total']==0

def test_csrf_and_manual_review_api():
    seed()
    with TestClient(A.app) as client:
        assert client.post('/api/mailbox/scan').status_code==403
        assert client.get('/api/mailbox').status_code==200
        assert client.post('/api/mailbox/scan',headers={'X-Local-Token':A.TOKEN}).status_code==400
        assert client.get('/api/mailbox/evidence').json()['total']==1

@pytest.mark.parametrize('subject,body',[
 ('Your Automatic Payment was Successful','Your account is in a good position. Careers at Example'),
 ('Remote opening of AI Strategic Consultant','We are hiring. Apply for this position.'),
 ('Which Example Systems career opportunities match your goals?','Your application could be next.'),
 ('Application key has been created','Your application key was generated.'),
 ('Senior Product Manager opportunities are available','Tips: thank you for applying.'),
 ("Here’s your Example ATS security code",'Verify your candidate account.'),
])
def test_marketing_and_transactional_mail_not_application(subject,body):
    assert M.evidence_bucket(subject,body)!=1

@pytest.mark.parametrize('subject,body',[
 ('Example Health Group: We received your application.','Thank you.'),
 ('Apply4Me Application Sent, Form Attached: Vice President at Example Learning',''),
 ('Your Application Has Been Received!',''),
 ('Example Consulting Phone Screen Invitation',''),
 ('Next Step: Advisor Application Questionnaire',''),
 ('An update on your application','We will not be moving forward with your candidacy.'),
 ('Alex, we have received your resume',''),
])
def test_application_correspondence_retained(subject,body):
    assert M.evidence_bucket(subject,body)==1

def test_refilter_keeps_all_evidence_and_review_decisions():
    seed('receipt');seed('alert');seed('reviewed')
    with C.db() as c:
        c.execute("UPDATE mailbox_evidence SET subject='New job alert',body='Jobs for you' WHERE id IN ('alert','reviewed')")
        c.execute("UPDATE mailbox_evidence SET decision='applied' WHERE id='reviewed'")
    M.refilter()
    assert M.evidence()['total']==1
    assert M.evidence(bucket=0)['total']==1
    assert M.evidence(show_all=True)['total']==3
    assert M.get_evidence('reviewed')['decision']=='applied'
