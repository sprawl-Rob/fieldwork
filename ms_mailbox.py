"""Read-only Microsoft Graph access and a local, review-first reconciliation queue."""
import asyncio, base64, calendar, hashlib, json, os, re, secrets, time
from datetime import datetime, timezone
from urllib.parse import urlencode, quote, urlsplit
import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
import core as C
import intelligence as AI
from discovery import SSL

router=APIRouter()
GRAPH='https://graph.microsoft.com/v1.0'
REDIRECT='http://localhost:8765/api/mailbox/callback'
SCOPES='https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read offline_access'
session={}
pending={}
scan_task=None
progress={'state':'idle','scanned':0,'candidates':0,'folders':0,'error':''}

def init():
    C.DATA.mkdir(parents=True,exist_ok=True)
    os.chmod(C.DATA,0o700)
    with C.db() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS mailbox_evidence (
          id TEXT PRIMARY KEY, account TEXT, message_id TEXT, subject TEXT, sender TEXT,
          received TEXT, folder TEXT, body TEXT, web_url TEXT, candidate INTEGER,
          result TEXT, matched_id INTEGER, decision TEXT DEFAULT 'pending', apply_note TEXT DEFAULT '')''')
    os.chmod(C.DATA/'jobs.sqlite3',0o600)
    with C.db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS mailbox_meta(key TEXT PRIMARY KEY,value TEXT)')
        version=c.execute("SELECT value FROM mailbox_meta WHERE key='filter_version'").fetchone()
    if not version or version[0]!='3':
        refilter()
        with C.db() as c:c.execute("INSERT OR REPLACE INTO mailbox_meta VALUES('filter_version','3')")

def config():
    path=C.DATA/'microsoft.json'
    return json.loads(path.read_text()) if path.exists() else {'tenant_id':'','client_id':''}

def cutoff():
    now=datetime.now(timezone.utc);month=now.month-6;year=now.year
    if month<1:month+=12;year-=1
    return now.replace(year=year,month=month,day=min(now.day,calendar.monthrange(year,month)[1])).isoformat()

def clean_body(message):
    body=message.get('body') or {};text=body.get('content','')
    if body.get('contentType','').lower()=='html':text=BeautifulSoup(text,'html.parser').get_text(' ',strip=True)
    return text[:40000]

def evidence_bucket(subject,body,companies=()):
    """1: application correspondence, 2: uncertain hiring mail, 0: alerts/unrelated.

    Employer names and ATS brands alone are never application evidence.
    """
    subject=re.sub(r'\s+',' ',subject).strip()
    body=re.sub(r'\s+',' ',body).strip()
    text=subject+' '+body
    if re.search(r'\b(?:rental|mortgage|loan|credit card|housing|lease) application\b|application.{0,25}\b\d+ .{0,40}(?:rd|road|street|st|drive|dr|avenue|ave)\b',text,re.I):return 0
    application=r'(?:your |the )?(?:job )?application'
    receipt=rf'(?:thank(?:s| you) (?:for )?(?:your (?:job )?application|applying)|(?:we(?: have|[’\x27]ve)?[ ,:]*)?(?:received|reviewed|reviewing|submitted|sent) {application}|{application}.{{0,35}}(?:received|submitted|sent|under review|being reviewed)|application (?:confirmation|received|update|status)|apply4me.{{0,65}}(?:application|applying)|(?:received|reviewing) your (?:resume|résumé))'
    interview=r'(?:(?:interview|phone screen|phone screening).{0,45}(?:invitation|invite|confirm|scheduled|availability)|(?:schedule|scheduling|confirm|invite|inviting|invitation|availability).{0,60}(?:interview|phone screen)|(?:your|our|the|an) (?:upcoming )?interview|next (?:step|round).{0,45}(?:interview|application|assessment)|offer (?:of employment|letter)|(?:your|an|the) (?:job|employment) offer)'
    if re.search(receipt+'|'+interview,subject,re.I):return 1
    # Alerts and account/marketing messages often quote generic application advice.
    noise=r'job alert|jobs? (?:for you|matching|matches|recommendations)|opportunit(?:y|ies).{0,45}(?:available|need to be filled|match your)|career opportunities match|new .{0,70} opportunity|remote opening|security code|verification code|verify your .{0,30}account|confirm your .{0,30}email|application key|password|automatic payment|receipt for|payment (?:received|successful)|booking confirmed|membership rewards|points pulse|subscription|\$\d'
    if re.search(noise,subject,re.I):return 0
    if re.search(receipt+'|'+interview,body,re.I):return 1
    hiring=bool(re.search(r'\b(application|applying|applied|candidate|candidacy|hiring|recruiter|recruitment|interview|role|position|resume|résumé)\b',text,re.I))
    decision=r'(?:not|no longer|won[’\x27]t|unable to).{0,30}(?:mov(?:e|ing)|proceed|pursu|consider)|other candidates|regret to inform|position.{0,35}(?:filled|closed)|withdraw.{0,35}application|thank you for your interest'
    if hiring and re.search(decision,text,re.I):return 1
    if re.search(r'\b(application|candidate portal|candidacy|assessment|recruiter|interview|phone screen)\b',text,re.I):return 2
    return 0

def candidate(subject,body,companies):
    return evidence_bucket(subject,body,companies)==1

def refilter():
    """Non-destructive migration: reviewed items and cached AI results stay intact."""
    with C.db() as c:
        rows=c.execute("SELECT id,subject,body,result FROM mailbox_evidence WHERE decision='pending'").fetchall()
        for row in rows:
            bucket=evidence_bucket(row['subject'] or '',row['body'] or '')
            if row['result']:
                result=json.loads(row['result']);bucket=0 if result.get('outcome')=='other' else 1
            c.execute('UPDATE mailbox_evidence SET candidate=? WHERE id=?',(bucket,row['id']))
    return len(rows)

async def token_request(fields):
    cfg=config()
    async with httpx.AsyncClient(verify=SSL,timeout=35) as client:
        r=await client.post('https://login.microsoftonline.com/'+cfg['tenant_id']+'/oauth2/v2.0/token',data={'client_id':cfg['client_id'],**fields})
    if r.status_code!=200:raise ValueError('Microsoft sign-in failed or expired. Check the app registration and connect again.')
    return r.json()

def keep_tokens(data):
    session.update(access_token=data['access_token'],expires=time.time()+int(data.get('expires_in',3600))-60)
    if data.get('refresh_token'):session['refresh_token']=data['refresh_token']

async def graph(url,params=None):
    if url.startswith('/'):url=GRAPH+url
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.netloc!='graph.microsoft.com' or not parsed.path.startswith('/v1.0/'):
        raise ValueError('Refused an unexpected Microsoft pagination URL')
    if not session.get('access_token'):raise ValueError('Connect Microsoft 365 first')
    if time.time()>=session.get('expires',0):
        if not session.get('refresh_token'):raise ValueError('Microsoft connection expired. Connect again.')
        keep_tokens(await token_request({'grant_type':'refresh_token','refresh_token':session['refresh_token'],'scope':SCOPES}))
    async with httpx.AsyncClient(verify=SSL,timeout=40) as client:
        for attempt in range(4):
            r=await client.get(url,params=params,headers={'Authorization':'Bearer '+session['access_token'],'Prefer':'IdType="ImmutableId", outlook.body-content-type="text"'})
            if r.status_code not in (429,503):break
            if attempt==3:break
            try:delay=min(30,max(1,int(r.headers.get('Retry-After','3'))))
            except ValueError:delay=3
            await asyncio.sleep(delay)
    if r.status_code in (401,403):raise ValueError('Microsoft denied access. Reconnect and check delegated Mail.Read and User.Read permissions.')
    if r.status_code!=200:raise ValueError(f'Microsoft mailbox request failed (HTTP {r.status_code}). Scan again to resume safely.')
    return r.json()

async def pages(url,params=None):
    seen=set()
    while url:
        if url in seen:raise ValueError('Microsoft returned a repeated pagination link')
        seen.add(url);data=await graph(url,params);params=None
        for row in data.get('value',[]):yield row
        url=data.get('@odata.nextLink')

async def scan():
    progress.update(state='running',scanned=0,candidates=0,folders=0,error='')
    try:
        account=session['account'];since=cutoff()
        with C.db() as c:companies=[r[0] for r in c.execute('SELECT DISTINCT company FROM jobs')]
        inbox=await graph('/me/mailFolders/inbox',{'$select':'id,displayName'})
        todo=[(inbox['id'],inbox.get('displayName','Inbox'))];visited=set()
        while todo:
            fid,path=todo.pop(0)
            if fid in visited:continue
            visited.add(fid);progress['folders']+=1
            root='/me/mailFolders/'+quote(fid,safe='')
            async for msg in pages(root+'/messages',{'$filter':'receivedDateTime ge '+since,'$select':'id,internetMessageId,subject,from,receivedDateTime,body,webLink','$top':'50'}):
                progress['scanned']+=1
                if not msg.get('receivedDateTime') or not C.parsedate(msg['receivedDateTime']):continue
                text=clean_body(msg);subject=msg.get('subject','');bucket=evidence_bucket(subject,text,companies);relevant=bucket>0
                if not relevant:continue
                progress['candidates']+=int(bucket==1)
                # Internet message ID survives folder moves; immutable ID is the fallback.
                key=hashlib.sha256((account+'\0'+(msg.get('internetMessageId') or msg['id'])).encode()).hexdigest()
                sender=(msg.get('from') or {}).get('emailAddress') or {}
                with C.db() as c:
                    c.execute('''INSERT OR IGNORE INTO mailbox_evidence
                      (id,account,message_id,subject,sender,received,folder,body,web_url,candidate)
                      VALUES (?,?,?,?,?,?,?,?,?,?)''',(key,account,msg['id'],subject,sender.get('address',''),msg['receivedDateTime'],path,text,msg.get('webLink',''),bucket))
            async for folder in pages(root+'/childFolders',{'$select':'id,displayName','$top':'100'}):
                todo.append((folder['id'],path+' / '+folder.get('displayName','Folder')))
        progress['state']='complete'
    except asyncio.CancelledError:progress['state']='cancelled'
    except Exception as e:progress.update(state='failed',error=str(e))

class Connection(BaseModel):
    tenant_id:str=Field(pattern=r'^[0-9a-fA-F-]{36}$')
    client_id:str=Field(pattern=r'^[0-9a-fA-F-]{36}$')

@router.get('/api/mailbox')
def state():
    init()
    with C.db() as c:
        counts=dict(c.execute('SELECT decision,count(*) FROM mailbox_evidence GROUP BY decision').fetchall())
        buckets={str(r[0]):r[1] for r in c.execute("SELECT COALESCE(candidate,1),count(*) FROM mailbox_evidence WHERE decision='pending' GROUP BY COALESCE(candidate,1)")}
    provider,model=AI.selection('email')
    return {'config':config(),'connected':bool(session.get('account')),'account':session.get('label',''),'progress':progress,'counts':counts,'buckets':buckets,'provider':provider,'model':model,'ai_ready':AI.available('email'),'redirect_uri':REDIRECT}

@router.post('/api/mailbox/connect')
def connect(body:Connection):
    if scan_task and not scan_task.done():raise HTTPException(409,'Stop the mailbox scan before reconnecting')
    C.save_json(C.DATA/'microsoft.json',body.model_dump())
    session.clear();pending.clear()
    verifier=secrets.token_urlsafe(64);state=secrets.token_urlsafe(32)
    pending.update(state=state,verifier=verifier,expires=time.time()+600)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return {'url':'https://login.microsoftonline.com/'+body.tenant_id+'/oauth2/v2.0/authorize?'+urlencode({'client_id':body.client_id,'response_type':'code','redirect_uri':REDIRECT,'response_mode':'query','scope':SCOPES,'state':state,'code_challenge':challenge,'code_challenge_method':'S256','prompt':'select_account'})}

@router.get('/api/mailbox/callback',response_class=HTMLResponse)
async def callback(state:str='',code:str='',error:str=''):
    if not state or not pending.get('state') or not secrets.compare_digest(state,pending['state']) or time.time()>pending['expires']:
        raise HTTPException(400,'Sign-in expired or invalid. Start again in Fieldwork.')
    verifier=pending['verifier'];pending.clear()
    if error or not code:return HTMLResponse('<h2>Microsoft connection was not completed.</h2><a href="http://127.0.0.1:8765/">Return to Fieldwork</a>',status_code=400)
    try:
        keep_tokens(await token_request({'grant_type':'authorization_code','code':code,'redirect_uri':REDIRECT,'code_verifier':verifier,'scope':SCOPES}))
        user=await graph('/me',{'$select':'id,displayName,mail,userPrincipalName'})
        session.update(account=config()['tenant_id']+':'+user['id'],label=user.get('mail') or user.get('userPrincipalName') or user.get('displayName','Connected'))
    except Exception:
        session.clear();raise HTTPException(400,'Connection failed. Check registration settings and try again.')
    return '<h2>Microsoft 365 connected read-only.</h2><p>Return to Fieldwork and open Microsoft 365 reconciliation to scan your inbox.</p><a href="http://127.0.0.1:8765/">Return to Fieldwork</a>'

@router.post('/api/mailbox/disconnect')
async def disconnect():
    if scan_task and not scan_task.done():
        scan_task.cancel();await scan_task
    session.clear();pending.clear()
    return {'message':'Disconnected. Tokens cleared; local review evidence retained.'}

@router.post('/api/mailbox/scan')
async def start_scan():
    global scan_task
    if not session.get('account'):raise HTTPException(400,'Connect Microsoft 365 first')
    if scan_task and not scan_task.done():raise HTTPException(409,'A mailbox scan is already running')
    init();progress.update(state='running',error='');scan_task=asyncio.create_task(scan())
    return {'message':'Scanning the past six months of Inbox and all nested folders locally'}

@router.post('/api/mailbox/cancel')
async def cancel():
    if scan_task and not scan_task.done():scan_task.cancel()
    return {'message':'Stopping mailbox scan'}

@router.get('/api/mailbox/evidence')
def evidence(offset:int=0,show_all:bool=False,bucket:int=1):
    init()
    with C.db() as c:
        if bucket not in (0,1,2):raise HTTPException(400,'Unknown review category')
        where='' if show_all else "WHERE decision='pending' AND COALESCE(candidate,1)="+str(bucket)
        total=c.execute('SELECT count(*) FROM mailbox_evidence '+where).fetchone()[0]
        rows=[dict(r) for r in c.execute('SELECT * FROM mailbox_evidence '+where+' ORDER BY received DESC LIMIT 30 OFFSET ?',(max(0,offset),))]
    for row in rows:row['result']=json.loads(row['result']) if row['result'] else None
    return {'items':rows,'total':total}

def get_evidence(eid):
    with C.db() as c:row=c.execute('SELECT * FROM mailbox_evidence WHERE id=?',(eid,)).fetchone()
    if not row:raise HTTPException(404,'Message not found')
    return dict(row)

@router.post('/api/mailbox/evidence/{eid}/classify')
async def classify(eid:str):
    row=get_evidence(eid)
    if row['result']:return json.loads(row['result'])
    if row['decision']!='pending':raise HTTPException(409,'This message has already been reviewed')
    try:
        result=(await AI.classify('Subject: '+row['subject']+'\nFrom: '+row['sender']+'\nReceived: '+row['received']+'\n\n'+row['body'])).model_dump()
    except Exception as e:raise HTTPException(400,str(e))
    matched=C.match_update(result['company'],result['role_title']) if result['company'] else None
    with C.db() as c:c.execute('UPDATE mailbox_evidence SET result=?,matched_id=?,candidate=? WHERE id=?',(json.dumps(result),matched,0 if result['outcome']=='other' else 1,eid))
    return result

class Approval(BaseModel):
    job_id:int|None=None
    status:str
    create:bool=False
    company:str=Field(default='',max_length=200)
    title:str=Field(default='',max_length=300)
    override:bool=False

@router.post('/api/mailbox/evidence/{eid}/apply')
def approve(eid:str,body:Approval):
    row=get_evidence(eid)
    if row['decision']!='pending':raise HTTPException(409,'This message has already been reviewed')
    if body.status not in C.STATUSES:raise HTTPException(400,'Choose a valid status')
    jid=body.job_id
    if body.create:
        if not body.company.strip() or not body.title.strip():raise HTTPException(400,'Enter a company and role title to create a record')
        jid,_=C.upsert({'company':body.company.strip(),'title':body.title.strip(),'source':'Microsoft 365 reconciliation'})
    if not jid:raise HTTPException(400,'Choose an opportunity or create one')
    result=json.loads(row['result']) if row['result'] else {}
    note='Email: '+row['subject']+'\n'+result.get('summary',row['body'][:1200])+'\nEvidence: '+row['received']+' · '+row['sender']
    try:message=C.update_status(jid,status=body.status,note=note,occurred_at=row['received'],provenance='reviewed mailbox',fingerprint=eid,force=body.override)
    except ValueError as e:raise HTTPException(400,str(e))
    with C.db() as c:c.execute("UPDATE mailbox_evidence SET decision='applied',matched_id=?,apply_note=? WHERE id=?",(jid,message,eid))
    return {'message':message}

@router.post('/api/mailbox/evidence/{eid}/dismiss')
def dismiss(eid:str):
    row=get_evidence(eid)
    if row['decision']!='pending':raise HTTPException(409,'This message has already been reviewed')
    with C.db() as c:c.execute("UPDATE mailbox_evidence SET decision='dismissed' WHERE id=?",(eid,))
    return {'message':'Dismissed from review; email unchanged'}
