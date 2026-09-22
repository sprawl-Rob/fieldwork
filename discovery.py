"""Source adapters. No generated listings; all records originate in fetched data."""
import asyncio, hashlib, html, ipaddress, json, os, re, socket, ssl, time
from datetime import datetime, timezone
from urllib.parse import urlsplit, quote, urljoin
import certifi, httpx
from bs4 import BeautifulSoup
from core import DATA, assess, now, family

NEXT_REQUEST={}
COOLDOWN={}
SSL=ssl.create_default_context(cafile=certifi.where())
HEADERS={'User-Agent':'PersonalJobSearch/1.0 (+local single-user job research)','Accept':'application/json, text/html;q=0.9'}

async def public_url(url):
    p=urlsplit(url)
    if p.scheme not in ['http','https'] or not p.hostname or p.username or p.password or p.port not in [None,80,443]: raise ValueError('Only public HTTP(S) job links are allowed')
    addresses=await asyncio.to_thread(socket.getaddrinfo,p.hostname,p.port or 443,type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses): raise ValueError('Private or local network links are not allowed')

async def fetch(url,params=None,ttl=0):
    key=hashlib.sha256((url+json.dumps(params,sort_keys=True)).encode()).hexdigest(); cache=DATA/'cache'/key
    if ttl and cache.exists() and time.time()-cache.stat().st_mtime<ttl: return json.loads(cache.read_text())
    async with httpx.AsyncClient(verify=SSL,timeout=25,headers=HEADERS,follow_redirects=False) as client:
        for hop in range(6):
            await public_url(url)
            host=urlsplit(url).hostname
            if 'linkedin.com' in host:
                if COOLDOWN.get(host,0)>time.time():raise SourceError(429,host+' (cooldown; no request sent)')
                delay=max(0,NEXT_REQUEST.get(host,0)-time.monotonic());NEXT_REQUEST[host]=time.monotonic()+delay+1.2
                if delay:await asyncio.sleep(delay)
                if COOLDOWN.get(host,0)>time.time():raise SourceError(429,host+' (cooldown; no request sent)')
            async with client.stream('GET',url,params=params) as response:
                if response.status_code in [301,302,303,307,308]:
                    url=urljoin(str(response.url),response.headers.get('location','')); params=None; continue
                body=bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body)>20_000_000: raise ValueError('Response exceeds 20 MB limit')
                result={'status':response.status_code,'url':str(response.url),'text':body.decode('utf-8',errors='replace')}
                if response.status_code==429:COOLDOWN[host]=time.time()+600
                if response.status_code>=400: raise SourceError(response.status_code,urlsplit(str(response.url)).hostname)
                if ttl: cache.write_text(json.dumps(result))
                return result
        raise ValueError('Too many redirects')

class SourceError(Exception):
    def __init__(self,status,host): self.status=status; super().__init__(f'HTTP {status} from {host}')

def plain(text):
    soup=BeautifulSoup(html.unescape(html.unescape(text or '')),'html.parser')
    for el in soup(['script','style','nav','footer']): el.decompose()
    return soup.get_text(' ',strip=True)

def job(title,company,location,url,source,**kw):
    mode=kw.pop('work_mode','unknown').lower()
    if 'remote' in location.lower(): mode='remote'
    if mode in ['onsite','on_site']: mode='onsite'
    return dict(title=title,company=company,location=location,url=url,source=source,work_mode=mode,**kw)

async def watch_board(w):
    provider=w['provider']; slug=quote(w['slug'],safe=''); name=w['company']
    url={'greenhouse':f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true','lever':f'https://api.lever.co/v0/postings/{slug}?mode=json','ashby':f'https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true'}[provider]
    data=json.loads((await fetch(url,ttl=900))['text']); out=[]
    for x in data if isinstance(data,list) else data.get('jobs',[]):
        if provider=='greenhouse':
            j=job(x['title'],name,x.get('location',{}).get('name',''),x['absolute_url'],provider,source_id=str(x['id']),summary=plain(x.get('content','')),posted=x.get('first_published',''))
        elif provider=='lever':
            j=job(x['text'],name,x.get('categories',{}).get('location',''),x['hostedUrl'],provider,source_id=x['id'],summary=plain(x.get('description','')+' '.join(z.get('content','') for z in x.get('lists',[]))),work_mode=x.get('workplaceType','unknown'),employment_type=x.get('categories',{}).get('commitment',''))
        else:
            if not x.get('isListed',True): continue
            j=job(x['title'],name,x.get('location',''),x['jobUrl'],provider,source_id=x.get('id',x['jobUrl']),summary=x.get('descriptionPlain',''),work_mode=x.get('workplaceType','remote' if x.get('isRemote') else 'unknown'),posted=x.get('publishedAt',''),employment_type=x.get('employmentType',''),salary=(x.get('compensation') or {}).get('scrapeableCompensationSalarySummary',''))
        j.update(industry=w.get('industry',''),company_size=w.get('size','unknown'));out.append(j)
    return out

async def remotive():
    data=json.loads((await fetch('https://remotive.com/api/remote-jobs',ttl=21600))['text'])
    return [job(x['title'],x['company_name'],x.get('candidate_required_location',''),x['url'],'Remotive',work_mode='remote',source_id=str(x['id']),summary=plain(x.get('description','')),posted=x.get('publication_date',''),salary=x.get('salary',''),employment_type=x.get('job_type','')) for x in data['jobs']]

async def remoteok():
    data=json.loads((await fetch('https://remoteok.com/api',ttl=21600))['text'])
    return [job(x['position'],x['company'],x.get('location',''),x.get('url',''),'RemoteOK',work_mode='remote',source_id=str(x['id']),summary=plain(x.get('description','')),posted=x.get('date',''),salary=f"{x.get('salary_min','')}–{x.get('salary_max','')}" if x.get('salary_min') else '') for x in data if x.get('position')]

async def muse(p,log):
    out=[]
    for page in range(p['max_pages']):
        log(f'The Muse: page {page+1}')
        data=json.loads((await fetch('https://www.themuse.com/api/public/jobs',{'page':page,'level':'Senior Level','category':'Software Engineering'},ttl=21600))['text'])
        for x in data.get('results',[]): out.append(job(x['name'],x['company']['name'],', '.join(l['name'] for l in x.get('locations',[])),x['refs']['landing_page'],'The Muse',source_id=str(x['id']),summary=plain(x.get('contents','')),posted=x.get('publication_date','')))
        if page+1>=data.get('page_count',0):break
    return out

async def linkedin(p,log,focus):
    out=[]
    for title in p['target_titles'][:6]:
        for remote in [True,False]:
            for page in range(p['max_pages']):
                log(f'LinkedIn: {title}, {"remote US" if remote else p["home_city"]}, page {page+1}')
                params={'keywords':(title+' '+focus).strip(),'location':'United States' if remote else p['home_city'],'distance':p['radius_miles'],'f_TPR':f'r{p["recency_days"]*86400}','start':page*25}
                if remote:params['f_WT']='2'
                try:
                    data=await fetch('https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search',params,ttl=21600)
                except Exception as e:
                    log(f'LinkedIn stopped with {len(out)} records retained: {e}', 'error')
                    return out
                soup=BeautifulSoup(data['text'],'html.parser'); cards=soup.select('.base-card')
                for card in cards:
                    t=card.select_one('.base-search-card__title'); c=card.select_one('.base-search-card__subtitle'); a=card.select_one('a.base-card__full-link'); loc=card.select_one('.job-search-card__location'); date=card.select_one('time')
                    if t and c and a: out.append(job(t.get_text(strip=True),c.get_text(strip=True),loc.get_text(strip=True) if loc else '',a['href'],'LinkedIn',work_mode='remote' if loc and 'remote' in loc.get_text().lower() else 'unknown',posted=date.get('datetime','') if date else '',source_id=card.get('data-entity-urn','')))
                if not cards:break
                await asyncio.sleep(.85)
    return out

async def builtin(p,log):
    out=[]
    for url in ['https://builtin.com/jobs/remote/dev-engineering','https://www.builtinboston.com/jobs/dev-engineering']:
        log('Built In: '+url)
        data=await fetch(url,ttl=21600); soup=BeautifulSoup(data['text'],'html.parser')
        def walk(x):
            if isinstance(x,list):
                for y in x:yield from walk(y)
            if isinstance(x,dict):
                if x.get('@type')=='JobPosting':yield x
                for v in x.values():
                    if isinstance(v,(dict,list)):yield from walk(v)
        for script in soup.select('script[type="application/ld+json"]'):
            try: entries=list(walk(json.loads(script.string or script.get_text())))
            except ValueError:continue
            for x in entries:
                loc=x.get('jobLocation',{}); loc=loc[0] if isinstance(loc,list) and loc else loc
                addr=loc.get('address',{}) if isinstance(loc,dict) else {}
                out.append(job(x['title'],x.get('hiringOrganization',{}).get('name','Unknown'),', '.join(str(addr.get(k,'')) for k in ['addressLocality','addressRegion']),x.get('url',url),'Built In',summary=plain(x.get('description','')),posted=x.get('datePosted',''),work_mode='remote' if x.get('jobLocationType')=='TELECOMMUTE' else 'unknown'))
    if not out:raise ValueError('No JobPosting data found; page format may have changed')
    return out

async def adzuna(p,log):
    if not os.getenv('ADZUNA_APP_ID') or not os.getenv('ADZUNA_APP_KEY'):raise ValueError('Not configured: add ADZUNA_APP_ID and ADZUNA_APP_KEY in .env')
    out=[]
    for title in p['target_titles'][:6]:
        for remote in [False,True]:
            for page in range(1,p['max_pages']+1):
                log(f'Adzuna: {title}, {"remote" if remote else "local"}, page {page}')
                params={'app_id':os.environ['ADZUNA_APP_ID'],'app_key':os.environ['ADZUNA_APP_KEY'],'what':title+(' remote' if remote else ''),'results_per_page':50,'max_days_old':p['recency_days'],'content-type':'application/json'}
                if not remote:params.update(where=p['home_city'],distance=round(p['radius_miles']*1.60934))
                data=json.loads((await fetch(f'https://api.adzuna.com/v1/api/jobs/us/search/{page}',params,ttl=21600))['text'])
                rows=data.get('results',[])
                for x in rows:out.append(job(x['title'],x.get('company',{}).get('display_name','Unknown'),x.get('location',{}).get('display_name',''),x['redirect_url'],'Adzuna',source_id=str(x['id']),summary=plain(x.get('description','')),posted=x.get('created',''),latitude=x.get('latitude'),longitude=x.get('longitude'),employment_type=x.get('contract_time',''),salary=f"${x.get('salary_min',0):,.0f}–${x.get('salary_max',0):,.0f}" if x.get('salary_min') else ''))
                if len(rows)<50:break
    return out

async def discover(p,log,focus=''):
    tasks=[]; enabled=p['sources']; semaphore=asyncio.Semaphore(3)
    async def run(name,fn):
        async with semaphore:
            try:
                rows=await fn();matched=[]
                for j in rows:
                    ok,score,why=assess(j,p)
                    if ok:j.update(score=score,rationale=why,role_family=family(j['title']),score_kind='rules');matched.append(j)
                log(f'{name}: {len(rows)} scanned · {len(matched)} matched')
                return matched
            except Exception as e:log(f'{name}: {type(e).__name__}: {e}','error');return []
    if enabled.get('watchlist'):
        for w in p['watchlist']:tasks.append(run(w['company']+' / '+w['provider'],lambda w=w:watch_board(w)))
    for name,fn in [('remotive',remotive),('remoteok',remoteok),('muse',lambda:muse(p,log)),('linkedin',lambda:linkedin(p,log,focus)),('builtin',lambda:builtin(p,log)),('adzuna',lambda:adzuna(p,log))]:
        if enabled.get(name):tasks.append(run(name,fn))
    groups=await asyncio.gather(*tasks)
    return [j for g in groups for j in g]

async def verify(j):
    try:
        r=await fetch(j['url'],ttl=900);text=plain(r['text']).lower();soup=BeautifulSoup(r['text'],'html.parser')
        if re.search(r'job (?:is )?no longer available|position has been filled|job has expired|no longer accepting applications|this job is closed',text):return 'closed','Explicit closed notice'
        if re.search(r'captcha|verify you are human|access denied|sign in to continue|security verification',text):return 'unknown','Access challenge; could not verify'
        old=urlsplit(j['url']);new=urlsplit(r['url'])
        if old.path.rstrip('/')!=new.path.rstrip('/') and re.fullmatch(r'/(?:jobs|careers|search|job-search)?/?',new.path): return 'unknown','Redirected to a general page; closure suspected'
        title_tokens=set(re.findall(r'\w+',j['title'].lower()))-{'of','and','the'}
        if len(text)>300 and title_tokens and sum(bool(re.search(r'\b'+re.escape(t)+r'\b',text)) for t in title_tokens)/len(title_tokens)>.7:
            # A matching title alone is insufficient: require application or structured posting evidence.
            if soup.select_one('a[href*="apply"],button[type="submit"]') or '"jobposting"' in r['text'].lower() or 'apply for this' in text:return 'open','Posting and application evidence found'
        return 'unknown','Page reachable; active posting not conclusively verified'
    except SourceError as e:
        return ('closed',f'HTTP {e.status}') if e.status in [404,410] else ('unknown',str(e))
    except Exception as e:return 'unknown',f'{type(e).__name__}: {e}'

async def enrich(j):
    """Read a real posting's description; never let LLM output substitute for page text."""
    if j.get('summary'):return
    r=await fetch(j['url'],ttl=21600);soup=BeautifulSoup(r['text'],'html.parser')
    section=soup.select_one('.show-more-less-html__markup, .description__text, [itemprop="description"]')
    if section:j['summary']=section.get_text(' ',strip=True)[:25000]
    for criterion in soup.select('.description__job-criteria-item'):
        heading=criterion.select_one('h3');value=criterion.select_one('.description__job-criteria-text')
        if heading and value:
            if heading.get_text(strip=True).lower()=='industries':j['industry']=value.get_text(' ',strip=True)
            if heading.get_text(strip=True).lower()=='employment type':j['employment_type']=value.get_text(' ',strip=True)
    top=soup.select_one('.top-card-layout__first-subline')
    if top:
        text=top.get_text(' ',strip=True).lower()
        if 'remote' in text:j['work_mode']='remote'
        elif 'hybrid' in text:j['work_mode']='hybrid'
        elif 'on-site' in text or 'onsite' in text:j['work_mode']='onsite'
    for script in soup.select('script[type="application/ld+json"]'):
        try:items=json.loads(script.string or script.get_text())
        except ValueError:continue
        items=items if isinstance(items,list) else [items]
        for item in items:
            if not isinstance(item,dict):continue
            if item.get('@type')=='JobPosting':
                if item.get('description'):j['summary']=plain(item['description'])[:25000]
                if item.get('employmentType'):j['employment_type']=str(item['employmentType'])
                if item.get('datePosted'):j['posted']=item['datePosted']
                if item.get('jobLocationType')=='TELECOMMUTE':j['work_mode']='remote'
