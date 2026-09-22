"""Durable records, profile-driven filtering, transparent baseline scoring."""
import hashlib, json, math, os, re, sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

BASE = Path(__file__).resolve().parent
DATA = Path(os.getenv('JOB_SEARCH_DATA', str(BASE / 'data')))
STATUSES = ['new','interested','applied','acknowledged','screening','interviewing','finals','offer','rejected','withdrawn','closed','lost','dismissed']
RANK = {s:i for i,s in enumerate(STATUSES[:8])}
TERMINAL = {'rejected','withdrawn','closed','dismissed'}

def now(): return datetime.now(timezone.utc).isoformat()
def parsedate(value):
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except (ValueError, TypeError): return None

def save_json(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp'); temp.write_text(json.dumps(value,indent=2)); temp.replace(path)

def profile(): return json.loads((DATA/'profile.json').read_text())
def settings():
    s=json.loads((DATA/'settings.json').read_text())
    for task in ['score','email','search']:s.setdefault(task+'_provider',s.get('provider','none'))
    return s

def init():
    DATA.mkdir(parents=True,exist_ok=True)
    for d in ['updates','archive','cache']: (DATA/d).mkdir(exist_ok=True)
    if not (DATA/'profile.json').exists(): save_json(DATA/'profile.json',json.loads((BASE/'profile.default.json').read_text()))
    if not (DATA/'settings.json').exists(): save_json(DATA/'settings.json',{'provider':'none','score_model':'','email_model':'','search_model':'','max_web_searches':5})
    with db() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, identity TEXT UNIQUE, title TEXT NOT NULL, company TEXT NOT NULL, location TEXT DEFAULT '', work_mode TEXT DEFAULT 'unknown', url TEXT DEFAULT '', source TEXT DEFAULT '', source_id TEXT DEFAULT '', salary TEXT DEFAULT '', posted TEXT DEFAULT '', summary TEXT DEFAULT '', industry TEXT DEFAULT '', company_size TEXT DEFAULT 'unknown', role_family TEXT DEFAULT '', employment_type TEXT DEFAULT '', score INTEGER, score_kind TEXT DEFAULT 'rules', rationale TEXT DEFAULT '', excluded INTEGER DEFAULT 0, status TEXT DEFAULT 'new', availability TEXT DEFAULT 'unknown', verification_note TEXT DEFAULT '', verified_at TEXT, created_at TEXT, updated_at TEXT, applied_date TEXT, status_at TEXT, user_locked INTEGER DEFAULT 0, cover_letter TEXT DEFAULT '', last_note TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS origins(id INTEGER PRIMARY KEY, job_id INTEGER REFERENCES jobs(id), source TEXT, source_id TEXT, url TEXT, UNIQUE(source,source_id,url));
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, job_id INTEGER REFERENCES jobs(id), kind TEXT, status TEXT, note TEXT, occurred_at TEXT, ingested_at TEXT, provenance TEXT, fingerprint TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, started_at TEXT, ended_at TEXT, state TEXT, focus TEXT, added INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES runs(id), at TEXT, level TEXT, message TEXT);
        CREATE TABLE IF NOT EXISTS imports(fingerprint TEXT PRIMARY KEY, result TEXT, created_at TEXT);
        ''')
        for column in ['facts_locked','dedupe_locked']:
            if column not in [r[1] for r in c.execute('PRAGMA table_info(jobs)')]:c.execute('ALTER TABLE jobs ADD COLUMN '+column+' INTEGER DEFAULT 0')
        c.execute("UPDATE runs SET state='interrupted',ended_at=? WHERE state='running'",(now(),))

@contextmanager
def db():
    c=sqlite3.connect(DATA/'jobs.sqlite3',timeout=15); c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA busy_timeout=15000')
    try:
        yield c; c.commit()
    except Exception:
        c.rollback(); raise
    finally: c.close()

def norm(s):
    s=re.sub(r'\([^)]*\)','',s.lower())
    s=re.sub(r'\b(incorporated|inc|llc|ltd|corporation|corp)\b','',s)
    return re.sub(r'[^a-z0-9]','',s)

def title_key(title):
    for pattern,replacement in [(r'\bCTO\b','chief technology officer'),(r'\bCPTO\b','chief product and technology officer'),(r'\bCIO\b','chief information officer'),(r'\bVP\b','vice president')]:
        title=re.sub(pattern,replacement,title,flags=re.I)
    return norm(re.sub(r'\bof\b','',title,flags=re.I).replace('&','and'))

def canonical(url):
    p=urlsplit(url)
    q=[(k,v) for k,v in parse_qsl(p.query) if not k.startswith('utm_') and k not in ['source','ref','trk','trackingId']]
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path.rstrip('/'),urlencode(sorted(q)),''))

def family(title):
    return 'Internal technology' if re.search(r'\b(CIO|information|IT|infrastructure|systems)\b',title,re.I) else 'Product & engineering'

def title_match(title,p):
    if any(x.lower() in title.lower() for x in p.get('title_exclusions',[])):return False
    if any(norm(t)==norm(title) for t in p['target_titles']): return True
    if re.search(r'\b(CTO|CPTO|CIO)\b|chief (?:product (?:and|&) )?(?:technology|information) officer',title,re.I): return True
    leadership=re.search(r'\b(vp|svp|evp|vice president|director|head|chief|executive)\b',title,re.I)
    tech=re.search(r'\b(technology|engineering|software|digital|information|IT|data|AI|platform|cyber|cybersecurity|innovation|technical)\b',title,re.I)
    return bool(leadership and tech) and not bool(re.search(r'\b(account executive|sales engineer)\b',title,re.I))

def industry_exclusion(job,p):
    text=' '.join(str(job.get(k,'') or '') for k in ['company','title','industry'])
    for word in dict.fromkeys(p['industry_patterns']+p['excluded_industries']):
        if re.search(r'(?<!\w)'+re.escape(word)+r'(?!\w)',text,re.I): return word
    return ''

def distance(lat,lon,p):
    a,b,c,d=map(math.radians,[lat,lon,p['home_lat'],p['home_lon']])
    return 3958.8*2*math.asin(math.sqrt(math.sin((c-a)/2)**2+math.cos(a)*math.cos(c)*math.sin((d-b)/2)**2))

def location_check(job,p):
    loc=job.get('location',''); mode=job.get('work_mode','unknown').lower()
    if mode=='remote' or re.search(r'\b(remote|worldwide|anywhere)\b',loc,re.I):
        if re.search(r'\b(US|USA|United States|worldwide|anywhere|North America|Americas|Massachusetts)\b',loc,re.I): return True,'Remote; confirm Massachusetts eligibility'
        if re.search(r'\b(UK|United Kingdom|Europe|EMEA|India|Australia|Canada|Germany|France|LATAM)\b',loc,re.I): return False,'Remote location excludes the US'
        return True,'Remote geography unspecified; confirm Massachusetts eligibility'
    if job.get('latitude') is not None and job.get('longitude') is not None:
        miles=distance(float(job['latitude']),float(job['longitude']),p)
        return miles<=p['radius_miles'],f'{miles:.1f} miles from home (straight-line)'
    if any(re.search(r'\b'+re.escape(t)+r'\b',loc,re.I) for t in p['local_towns']):
        if re.search(r'\b(CA|TX|CO|MD|VA|WI|California|Texas|Colorado|Maryland|Virginia|Wisconsin)\b',loc): return False,'Different state'
        return True,'Nearby town; exact commute needs confirmation'
    if job.get('source')=='LinkedIn' and mode=='unknown': return True,'Search candidate only; remote eligibility not confirmed'
    if loc.strip(): return False,'Outside nearby town list or location not established'
    return True,'Location unknown; review required'

def assess(j,p):
    reasons=[]; score=48
    if not title_match(j['title'],p): return False,0,'Outside technology leadership targets'
    blocked=industry_exclusion(j,p)
    if blocked: return False,0,'Excluded employer industry/title: '+blocked
    fits,why=location_check(j,p)
    if not fits: return False,0,why
    dt=parsedate(j.get('posted'))
    if dt and (datetime.now(timezone.utc)-dt).days>p['recency_days']: return False,0,'Outside recency window'
    if re.search(r'\b(CTO|CPTO|CIO)\b|chief .*officer',j['title'],re.I): score+=23; reasons.append('Executive technology ownership +23')
    elif re.search(r'vice president|\b[ES]?VP\b|head of',j['title'],re.I): score+=16; reasons.append('Senior technology leadership +16')
    else: score+=7; reasons.append('Technology leadership +7; confirm scope')
    if j.get('work_mode')=='remote': score+=12; reasons.append('Remote preference +12')
    if 'nonprofit' in j.get('industry','').lower() or 'non-profit' in j.get('industry','').lower(): score+=12; reasons.append('Nonprofit employer +12')
    if j.get('company_size')=='small': score+=8; reasons.append('Smaller organization +8')
    if re.search(r'contract|fractional|interim|part.?time|temporary',j.get('employment_type','')+' '+j['title'],re.I): score-=18; reasons.append('Not full-time −18')
    reasons.append(why)
    if not dt: reasons.append('Posting date unknown')
    if not j.get('industry'): reasons.append('Employer industry needs review; provisional ranking')
    if not j.get('industry') or j.get('work_mode')=='unknown':
        score=min(score,64);reasons.append('Rank capped at 64 pending missing criteria')
    return True,max(0,min(100,score)),' · '.join(reasons)

def upsert(j):
    stamp=now(); url=canonical(j.get('url','')); source=j.get('source','manual'); sid=str(j.get('source_id',''))
    identity=hashlib.sha256((url or '|'.join([norm(j['company']),norm(j['title']),j.get('location','')])).encode()).hexdigest()
    with db() as c:
        found=c.execute('SELECT job_id FROM origins WHERE url=? OR (source=? AND source_id=? AND source_id<>\'\')',(url,source,sid)).fetchone() if url else None
        existing=c.execute('SELECT * FROM jobs WHERE id=?',(found[0],)).fetchone() if found else c.execute('SELECT * FROM jobs WHERE identity=?',(identity,)).fetchone()
        if not existing:
            # Cross-source merging requires matching company, title AND location. Same-source distinct requisitions stay separate.
            candidates=c.execute('SELECT * FROM jobs').fetchall()
            existing=next((r for r in candidates if norm(r['company'])==norm(j['company']) and title_key(r['title'])==title_key(j['title']) and norm(r['location'])==norm(j.get('location','')) and r['source']!=source and not r['dedupe_locked']),None)
            if not existing:
                placeholders=[r for r in candidates if norm(r['company'])==norm(j['company']) and r['title'].lower() in ['unknown role','unspecified','unknown'] and not r['dedupe_locked']]
                if len(placeholders)==1:existing=placeholders[0]
        cols=['title','company','location','work_mode','url','source','source_id','salary','posted','summary','industry','company_size','employment_type','score','score_kind','rationale','excluded','role_family']
        values={k:j[k] for k in cols if k in j}; values['updated_at']=stamp
        if existing:
            jid=existing['id']
            if existing['facts_locked']:
                for k in ['industry','company_size','work_mode','location','score','rationale','excluded','score_kind']:values.pop(k,None)
            # Keep first source as canonical; retain alternate URLs in origins.
            if existing['url']:
                for k in ['url','source','source_id']: values.pop(k,None)
            values={k:v for k,v in values.items() if v not in ('',None) or k=='score'}
            c.execute('UPDATE jobs SET '+','.join(k+'=?' for k in values)+' WHERE id=?',[*values.values(),jid])
        else:
            values.update(identity=identity,created_at=stamp,status_at=stamp)
            c.execute('INSERT INTO jobs('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',list(values.values())); jid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute('INSERT INTO events(job_id,kind,status,note,occurred_at,ingested_at,provenance) VALUES(?,?,?,?,?,?,?)',(jid,'discovered','new','Discovered via '+source,stamp,stamp,source))
        if url: c.execute('INSERT OR IGNORE INTO origins(job_id,source,source_id,url) VALUES(?,?,?,?)',(jid,source,sid,url))
    return jid,existing is None

def update_status(jid,status=None,note='',occurred_at=None,provenance='user',force=False,applied_date=None,fingerprint=None,cover_letter=None):
    if status and status not in STATUSES: raise ValueError('Unknown status')
    if occurred_at and not parsedate(occurred_at): raise ValueError('Invalid evidence date')
    if applied_date and not parsedate(applied_date): raise ValueError('Invalid application date')
    stamp=now(); at=occurred_at or stamp
    with db() as c:
        row=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        if not row: raise ValueError('Listing not found')
        if fingerprint and c.execute('SELECT 1 FROM events WHERE fingerprint=?',(fingerprint,)).fetchone(): return 'Already imported'
        old=row['status']; accepted=True; reason=''
        if status and status!=old and not force:
            if row['user_locked'] and provenance!='user': accepted=False; reason='User correction protected'
            elif old in TERMINAL: accepted=False; reason='Terminal status protected'
            elif parsedate(at)<parsedate(row['status_at']) and not (provenance=='reviewed mailbox' and old in ['new','interested']): accepted=False; reason='Older evidence retained without changing status'
            elif old!='lost' and RANK.get(status,99)<RANK.get(old,0): accepted=False; reason='Earlier pipeline stage retained as evidence'
        result=status if status and accepted else old
        c.execute('INSERT INTO events(job_id,kind,status,note,occurred_at,ingested_at,provenance,fingerprint) VALUES(?,?,?,?,?,?,?,?)',(jid,'status' if status and accepted else 'note',result,(note+(' ['+reason+']' if reason else '')).strip(),at,stamp,provenance,fingerprint))
        fields={'updated_at':stamp,'last_note':note or reason or ('Status: '+result)}
        if status and accepted:
            fields.update(status=result,status_at=at)
            if force: fields['user_locked']=1
            if result in RANK and RANK[result]>=RANK['applied'] and not row['applied_date']: fields['applied_date']=applied_date or at[:10]
        if applied_date and (force or not row['applied_date']): fields['applied_date']=applied_date
        if cover_letter is not None: fields['cover_letter']=cover_letter
        c.execute('UPDATE jobs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',[*fields.values(),jid])
    return reason or 'Updated'

def match_update(company,title=''):
    with db() as c: rows=[dict(r) for r in c.execute('SELECT * FROM jobs')]
    rows=[r for r in rows if norm(r['company'])==norm(company)]
    if title:
        exact=[r for r in rows if title_key(r['title'])==title_key(title)]
        if exact: rows=exact
        else:
            tokens=set(re.findall(r'\w+',title.lower()))
            rows=[r for r in rows if len(tokens & set(re.findall(r'\w+',r['title'].lower())))/max(1,len(tokens | set(re.findall(r'\w+',r['title'].lower()))))>=.7]
    return rows[0]['id'] if len(rows)==1 else None

def mark_lost(p):
    with db() as c: rows=[dict(r) for r in c.execute("SELECT * FROM jobs WHERE status IN ('applied','acknowledged') AND applied_date IS NOT NULL AND user_locked=0")]
    for r in rows:
        application=parsedate(r['applied_date']); evidence=parsedate(r['status_at'])
        # An old application is not ghosted immediately after fresh evidence.
        if application and (datetime.now(timezone.utc)-max(application,evidence)).days>=p['lost_after_days']:
            update_status(r['id'],'lost',f"No new stage evidence for {p['lost_after_days']} days",provenance='silence rule')
