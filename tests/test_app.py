import asyncio,json,os,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import core as C
import discovery as D
import intelligence as AI
import app as A
from fastapi.testclient import TestClient

@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(C,'DATA',tmp_path);monkeypatch.setattr(D,'DATA',tmp_path);C.init()
@pytest.fixture
def j():return {'company':'Example Software','title':'CTO','location':'United States','work_mode':'remote','url':'https://example.org/jobs/123','source':'greenhouse','source_id':'123','summary':'Build useful things','industry':'technology'}
def row(jid):
    with C.db() as c:return dict(c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
@pytest.mark.parametrize('title',['CTO','CPTO','Chief Information Officer','VP Engineering','Director of Technology','Head of Information Systems','VP of Software Engineering'])
def test_leadership_titles(title):assert C.title_match(title,C.profile())
@pytest.mark.parametrize('title',['Senior Software Engineer','Sales Engineer','Senior Director Analyst','Account Executive','Medical Director'])
def test_non_targets(title):assert not C.title_match(title,C.profile())
@pytest.mark.parametrize('company',['Example Healthcare','A Cryptocurrency Firm','Casino Games','Financial Services Inc','Example Biotech','Hospital Foundation'])
def test_industry_blocks(company,j):j['company']=company;assert not C.assess(j,C.profile())[0]
def test_customer_industry_not_blocked(j):j['summary']='Our cybersecurity software serves hospitals and banks';assert C.assess(j,C.profile())[0]
def test_remote_us_allowed(j):assert C.assess(j,C.profile())[0]
def test_remote_canada_not_allowed(j):j['location']='Canada';assert not C.assess(j,C.profile())[0]
def test_local_outside_radius(j):j.update(work_mode='onsite',location='Albany, NY');assert not C.assess(j,C.profile())[0]
def test_local_nearby(j):j.update(work_mode='hybrid',location='Cambridge, MA');assert C.assess(j,C.profile())[0]
def test_unknown_date_retained(j):assert 'date unknown' in C.assess(j,C.profile())[2].lower()
def test_old_listing_filtered(j):j['posted']='2020-01-01';assert not C.assess(j,C.profile())[0]
def test_full_time_preferred(j):a=C.assess(j,C.profile())[1];j['employment_type']='contract';assert C.assess(j,C.profile())[1]==a-18

def test_duplicate_url_normalized(j):
    jid,new=C.upsert(j);j['url']+='?utm_source=test';assert C.upsert(j)==(jid,False)
def test_cross_board_dedupe(j):
    jid,_=C.upsert(j);j.update(company='Example Software Inc.',source='lever',source_id='abc',url='https://example.org/another');assert C.upsert(j)==(jid,False)
    with C.db() as c:assert c.execute('SELECT count(*) FROM origins').fetchone()[0]==2
def test_distinct_requisitions_not_merged(j):
    jid,_=C.upsert(j);j.update(source_id='124',url='https://example.org/jobs/124');assert C.upsert(j)[0]!=jid

def test_status_no_downgrade(j):
    jid,_=C.upsert(j);C.update_status(jid,'interviewing');C.update_status(jid,'acknowledged',provenance='email');assert row(jid)['status']=='interviewing'
def test_terminal_protection(j):
    jid,_=C.upsert(j);C.update_status(jid,'withdrawn');C.update_status(jid,'interviewing');assert row(jid)['status']=='withdrawn'
def test_force_correction_locked(j):
    jid,_=C.upsert(j);C.update_status(jid,'rejected');C.update_status(jid,'applied',force=True);C.update_status(jid,'offer',provenance='email');assert row(jid)['status']=='applied'
def test_old_evidence_retained(j):
    jid,_=C.upsert(j);C.update_status(jid,'applied');C.update_status(jid,'rejected',occurred_at='2000-01-01');assert row(jid)['status']=='applied'
    with C.db() as c:assert c.execute('SELECT count(*) FROM events WHERE job_id=?',(jid,)).fetchone()[0]==3

def test_lost_revivable(j):
    jid,_=C.upsert(j);C.update_status(jid,'lost');C.update_status(jid,'interviewing',provenance='email');assert row(jid)['status']=='interviewing'
def test_event_idempotency(j):
    jid,_=C.upsert(j);C.update_status(jid,'applied',fingerprint='one');C.update_status(jid,'applied',fingerprint='one')
    with C.db() as c:assert c.execute('SELECT count(*) FROM events WHERE fingerprint=?',('one',)).fetchone()[0]==1

def test_ambiguous_company_not_matched(j):
    C.upsert(j);j.update(title='VP Technology',source_id='124',url='https://example.org/jobs/124');C.upsert(j);assert C.match_update('Example Software') is None

def test_import_retry_and_idempotency(j,tmp_path):
    path=tmp_path/'update.json';path.write_text(json.dumps({'company':j['company'],'title':j['title'],'status':'applied'}))
    assert asyncio.run(A.ingest_file(path))=='unmatched'
    C.upsert(j);assert asyncio.run(A.ingest_file(path))=='processed';assert asyncio.run(A.ingest_file(path))=='duplicate'

def test_import_create(tmp_path):
    path=tmp_path/'update.json';path.write_text(json.dumps({'company':'New Org','title':'CTO','status':'applied','create_if_missing':True}))
    assert asyncio.run(A.ingest_file(path))=='processed';assert C.match_update('New Org','CTO')

@pytest.mark.parametrize('code,state',[(404,'closed'),(410,'closed'),(403,'unknown'),(429,'unknown')])
def test_verify_http(monkeypatch,j,code,state):
    async def fake(*a,**k):raise D.SourceError(code,'example.org')
    monkeypatch.setattr(D,'fetch',fake);assert asyncio.run(D.verify(j))[0]==state

def test_redirect_is_uncertain(monkeypatch,j):
    async def fake(*a,**k):return {'status':200,'url':'https://example.org/jobs','text':'All our career opportunities'}
    monkeypatch.setattr(D,'fetch',fake);assert asyncio.run(D.verify(j))[0]=='unknown'

def test_explicit_closed(monkeypatch,j):
    async def fake(*a,**k):return {'status':200,'url':j['url'],'text':'This job has expired'}
    monkeypatch.setattr(D,'fetch',fake);assert asyncio.run(D.verify(j))[0]=='closed'

def test_unreachable_kept(monkeypatch,j):
    async def fake(*a,**k):raise TimeoutError('network timeout')
    monkeypatch.setattr(D,'fetch',fake);assert asyncio.run(D.verify(j))[0]=='unknown'

def test_private_urls_blocked():
    with pytest.raises(ValueError):asyncio.run(D.public_url('http://127.0.0.1/secrets'))
    with pytest.raises(ValueError):asyncio.run(D.public_url('file:///etc/passwd'))

def test_api_lifecycle_and_csrf(j):
    with TestClient(A.app) as c:
        assert c.post('/api/jobs',json=j).status_code==403
        assert c.get('/',headers={'host':'evil.example'}).status_code==403
        h={'x-local-token':A.TOKEN}
        r=c.post('/api/jobs',json={k:v for k,v in j.items() if k not in ['source','source_id']},headers=h);assert r.status_code==200
        jid=r.json()['id'];assert c.get('/api/jobs/'+str(jid)+'/brief').status_code==200
        c.patch('/api/jobs/'+str(jid),json={'status':'applied'},headers=h)
        assert c.get('/api/analytics').json()['applied']==1
        assert c.get('/api/export').status_code==200
        assert c.get('/').headers['cache-control']=='no-store'
        assert c.post('/api/cancel',headers=h).status_code==200

def test_1000_requests_close_db_handles():
    import subprocess
    def count():
        if Path('/proc/self/fd').exists():return len(list(Path('/proc/self/fd').iterdir()))
        result=subprocess.run(['lsof','-p',str(os.getpid()),'-F','f'],capture_output=True,text=True)
        assert result.returncode==0, result.stderr
        count=len([line for line in result.stdout.splitlines() if line.startswith('f') and line[1:].isdigit()])
        assert count>0
        return count
    with TestClient(A.app) as c:
        for _ in range(10):c.get('/api/state')
        before=count()
        for _ in range(1000):assert c.get('/api/state').status_code==200
        assert count()<=before

def test_scoring_bad_schema_keeps_baseline(monkeypatch,j):
    monkeypatch.setattr(AI,'available',lambda:True)
    async def fake(*a,**k):raise ValueError('invalid output')
    monkeypatch.setattr(AI,'structured',fake);j.update(score=78,score_kind='rules');logs=[]
    asyncio.run(AI.score_jobs([j],C.profile(),lambda *a:logs.append(a)))
    assert j['score']==78 and logs

def test_unknown_industry_not_a_strong_match(j):
    j['industry']='';assert C.assess(j,C.profile())[1]<65

def test_linkedin_query_does_not_confirm_remote(j):
    j.update(source='LinkedIn',work_mode='unknown',location='Albany, NY')
    allowed,score,why=C.assess(j,C.profile());assert allowed and score<65 and 'not confirmed' in why

def test_placeholder_folds_into_real_listing(j):
    old,_=C.upsert({'company':j['company'],'title':'Unknown role','source':'Update import'})
    C.update_status(old,'applied')
    assert C.upsert(j)==(old,False)
    assert row(old)['status']=='applied' and row(old)['title']=='CTO' and row(old)['url']==j['url']

def test_user_facts_survive_fetch(j):
    with TestClient(A.app) as c:
        jid,_=C.upsert(j)
        h={'x-local-token':A.TOKEN}
        assert c.patch(f'/api/jobs/{jid}/facts',json={'industry':'healthcare','location':'United States','work_mode':'remote','company_size':'small'},headers=h).status_code==200
        C.upsert(j);assert row(jid)['industry']=='healthcare' and row(jid)['excluded']==1

def test_split_merged_sources(j):
    with TestClient(A.app) as c:
        jid,_=C.upsert(j);j.update(source='lever',source_id='abc',url='https://example.org/another');C.upsert(j)
        origins=c.get(f'/api/jobs/{jid}').json()['origins'];oid=next(x['id'] for x in origins if x['source']=='lever')
        r=c.post(f'/api/origins/{oid}/separate',headers={'x-local-token':A.TOKEN});assert r.status_code==200
        assert r.json()['id']!=jid
        assert C.upsert(j)==(r.json()['id'],False)

def test_email_duplicate_updates(j):
    with TestClient(A.app) as c:
        jid,_=C.upsert(j);h={'x-local-token':A.TOKEN};body={'status':'acknowledged','evidence_id':'messagehash','note':'Application received'}
        assert c.patch(f'/api/jobs/{jid}',json=body,headers=h).json()['message']=='Updated'
        assert c.patch(f'/api/jobs/{jid}',json=body,headers=h).json()['message']=='Already imported'

def test_linkedin_metadata_enrichment(monkeypatch,j):
    async def fake(*a,**k):return {'status':200,'url':j['url'],'text':'<div class="description__job-criteria-item"><h3>Industries</h3><span class="description__job-criteria-text">Financial Services</span></div><div class="show-more-less-html__markup">We are a lender.</div>'}
    monkeypatch.setattr(D,'fetch',fake);j['summary']='';asyncio.run(D.enrich(j));assert j['industry']=='Financial Services';assert not C.assess(j,C.profile())[0]

def test_encoded_html_is_readable():
    assert D.plain('&lt;p&gt;Hello &amp;amp; goodbye&lt;/p&gt;')=='Hello & goodbye'

@pytest.mark.parametrize('provider,payload',[
 ('greenhouse',{'jobs':[{'id':12,'title':'CTO','location':{'name':'Remote US'},'absolute_url':'https://example.org/12','content':'&lt;p&gt;Build a team&lt;/p&gt;'}]}),
 ('lever',[{'id':'12','text':'CTO','categories':{'location':'Remote US','commitment':'Full-time'},'hostedUrl':'https://example.org/12','description':'Build a team','workplaceType':'remote'}]),
 ('ashby',{'jobs':[{'title':'CTO','location':'Remote US','jobUrl':'https://example.org/12','descriptionPlain':'Build a team','workplaceType':'Remote','isListed':True}]}),
])
def test_ats_shapes(monkeypatch,provider,payload):
    async def fake(*a,**k):return {'status':200,'text':json.dumps(payload)}
    monkeypatch.setattr(D,'fetch',fake)
    jobs=asyncio.run(D.watch_board({'provider':provider,'slug':'example','company':'Example','industry':'technology'}))
    assert len(jobs)==1 and jobs[0]['title']=='CTO' and jobs[0]['summary']=='Build a team'

def test_title_abbreviation_update_match(j):
    jid,_=C.upsert(j);assert C.match_update(j['company'],'Chief Technology Officer')==jid

def test_complete_search_run(monkeypatch,j):
    async def discover(*a,**k):return [{**j,'score':83,'rationale':'Executive role','score_kind':'rules'}]
    async def verify(*a,**k):return ('open','Test posting evidence')
    monkeypatch.setattr(D,'discover',discover);monkeypatch.setattr(D,'verify',verify)
    with C.db() as c:
        c.execute("INSERT INTO runs(started_at,state,focus) VALUES(?,'running','test')",(C.now(),));rid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    asyncio.run(A.search_run(rid,A.SearchRequest()))
    with C.db() as c:
        run=c.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone();job=c.execute('SELECT * FROM jobs').fetchone()
    assert run['state']=='completed' and run['added']==1 and job['availability']=='open'
