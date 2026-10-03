#!/usr/bin/env python3
"""Archive publicly available sources for programs explicitly active in metadata."""
import argparse, concurrent.futures, datetime as dt, gzip, hashlib, html, io, json, os, pathlib, re, shutil, subprocess, tarfile, threading, time, urllib.parse
ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / 'sources'
WORK = ROOT / 'work' / 'source-fetch'
OUT.mkdir(exist_ok=True); WORK.mkdir(parents=True, exist_ok=True)
LOCK = threading.Lock()
UA = 'Mozilla/5.0 (compatible; PublicSourceArchive/1.0)'

def stamp(): return dt.datetime.now(dt.timezone.utc).isoformat()
def digest(s): return hashlib.sha256(s.encode()).hexdigest()[:24]
def safe(s): return re.sub(r'[^a-zA-Z0-9._-]', '_', s)[:160]
def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix+'.tmp'); tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False)+'\n'); tmp.replace(path)
def relative(path): return str(path.relative_to(ROOT))
def download(url, target=None, retries=3, timeout=90):
    target = target or WORK / (digest(url)+'.response')
    target.parent.mkdir(parents=True, exist_ok=True)
    last = ''
    for attempt in range(retries):
        proc = subprocess.run(['curl','--location','--silent','--show-error','--compressed','--connect-timeout','20','--max-time',str(timeout),'-A',UA,'--output',str(target),'--write-out','%{http_code}',url], capture_output=True, text=True)
        code = proc.stdout[-3:]
        if proc.returncode == 0 and code.startswith('2'): return target, int(code)
        last = f'HTTP {code}; curl {proc.returncode}: {proc.stderr[:200]}'
        if code in ('404','410'): break
        time.sleep(min(2**attempt, 4))
    raise RuntimeError(last)
def get_json(url):
    path, _ = download(url)
    try: return json.loads(path.read_bytes())
    finally: path.unlink(missing_ok=True)
def is_active(p, now):
    def parsed(x): return dt.datetime.fromisoformat(x.replace('Z','+00:00'))
    return p.get('isPaused') is False and (not p.get('endDate') or parsed(p['endDate']) > now) and (not p.get('launchDate') or parsed(p['launchDate']) <= now)
def inventory(refresh=False):
    snapshot = OUT/'metadata'/'directory.json'
    if refresh or not snapshot.exists():
        try: programs = get_json('https://immunefi.com/public-api/bounties.json'); origin='https://immunefi.com/public-api/bounties.json'
        except Exception as e: programs=json.loads((ROOT/'projects.json').read_text());origin='projects.json'; print('Live metadata unavailable:',e,flush=True)
        now=dt.datetime.now(dt.timezone.utc)
        active=[p for p in programs if is_active(p,now)]
        save_json(snapshot,programs)
        save_json(OUT/'metadata'/'selection.json', {'selected_at':now.isoformat(),'origin':origin,'rule':'isPaused is explicitly false; launchDate <= selection time (or null); endDate > selection time (or null)','active_programs':[p['slug'] for p in active],'excluded':[{'slug':p['slug'],'isPaused':p.get('isPaused'),'launchDate':p.get('launchDate'),'endDate':p.get('endDate')} for p in programs if not is_active(p,now)]})
        for p in active: save_json(OUT/'metadata'/'programs'/(p['slug']+'.json'),p)
    programs=json.loads(snapshot.read_text()); selected=set(json.loads((OUT/'metadata'/'selection.json').read_text())['active_programs'])
    entries={}
    def add(url, slug, asset=None, field=None):
        if not isinstance(url,str):return
        url=html.unescape(url.strip()).rstrip('.,;')
        if not url.startswith(('http://','https://')):return
        key=digest(url); e=entries.setdefault(key,{'id':key,'url':url,'programs':[],'assets':[],'metadata_fields':[]})
        if slug not in e['programs']:e['programs'].append(slug)
        if asset:e['assets'].append({'program':slug,**asset})
        if field:e['metadata_fields'].append({'program':slug,'field':field})
    for p in programs:
        if p['slug'] not in selected:continue
        for a in p.get('assets',[]):add(a.get('url'),p['slug'],a)
        # Supplement in-scope URLs with repository references explicitly supplied by the program.
        for field in ('githubUrl','assetsBodyV2','programOverview'):
            value=p.get(field) or ''
            for u in re.findall(r'https?://(?:www\.)?(?:github\.com|gitlab\.com|bitbucket\.org)/[^\s<>"\]\)]+',value):add(u,p['slug'],field=field)
    save_json(OUT/'inventory.json',list(entries.values()))
    return entries

REPO_CACHE={}; REPO_LOCK=threading.Lock(); SNAP_LOCKS={}; SNAP_GUARD=threading.Lock()
def repo_info(owner, repo):
    key=(owner.lower(),repo.lower())
    with REPO_LOCK: cached=REPO_CACHE.get(key)
    if cached:return cached
    info=get_json(f'https://api.github.com/repos/{owner}/{repo}')
    with REPO_LOCK:REPO_CACHE[key]=info
    return info

def archive_repo(owner, repo, ref):
    info=repo_info(owner,repo); owner,repo=info['full_name'].split('/')
    commit=get_json(f'https://api.github.com/repos/{owner}/{repo}/commits/{urllib.parse.quote(ref,safe="")}')['sha']
    dest=OUT/'github'/safe(owner)/safe(repo)/commit
    with SNAP_GUARD: lock=SNAP_LOCKS.setdefault(str(dest),threading.Lock())
    with lock:
        index=dest/'snapshot.json'
        if index.exists():return json.loads(index.read_text())
        dest.mkdir(parents=True,exist_ok=True)
        tmp=WORK/(digest(str(dest))+'.tar.gz')
        download(f'https://codeload.github.com/{owner}/{repo}/tar.gz/{commit}',tmp,timeout=600)
        file_index=[];modules=None
        with tarfile.open(tmp,'r:gz') as tf:
            for member in tf:
                parts=member.name.split('/',1)
                if len(parts)<2 or not parts[1]:continue
                file_index.append({'path':parts[1],'bytes':member.size,'type':member.type.decode('ascii','replace')})
                if parts[1]=='.gitmodules' and member.isfile():modules=tf.extractfile(member).read().decode('utf-8','replace')
        if not file_index:raise RuntimeError('Empty repository archive')
        chunks=[];sha=hashlib.sha256();n=0
        with tmp.open('rb') as source:
            while block:=source.read(45*1024*1024):
                n+=1;part=dest/('source.tar.gz' if tmp.stat().st_size<=45*1024*1024 else f'source.tar.gz.part{n:03d}')
                part.write_bytes(block);sha.update(block);chunks.append(relative(part))
        tmp.unlink()
        save_json(dest/'files.json',file_index)
        result={'repository':info['html_url'],'requested_ref':ref,'commit':commit,'fetched_at':stamp(),'archive_parts':chunks,'archive_sha256':sha.hexdigest(),'file_count':len(file_index),'file_index':relative(dest/'files.json'),'submodule_configuration':modules,'snapshot':relative(index)}
        save_json(index,result);return result

def github(entry):
    parts=urllib.parse.unquote(urllib.parse.urlsplit(entry['url']).path).strip('/').split('/')
    if len(parts)<2 or not parts[1]:
        owner=parts[0]
        if '...' in owner:raise RuntimeError('Truncated upstream GitHub URL in metadata')
        repos=[];page=1
        while True:
            batch=get_json(f'https://api.github.com/users/{owner}/repos?per_page=100&page={page}')
            repos.extend(batch)
            if len(batch)<100:break
            page+=1
        return {'status':'fetched','kind':'github_organization','repositories':[archive_repo(*r['full_name'].split('/'),r['default_branch']) for r in repos if r.get('size',0)>0]}
    owner,repo=parts[:2];repo=repo.removesuffix('.git');info=repo_info(owner,repo);ref=info['default_branch'];path=''
    if len(parts)>3 and parts[2] in ('blob','tree'):
        # Resolve longest valid ref prefix; branches can contain slashes.
        tail=parts[3:]; resolved=None
        for count in range(1,len(tail)+1):
            candidate='/'.join(tail[:count])
            try:
                get_json(f'https://api.github.com/repos/{owner}/{repo}/commits/{urllib.parse.quote(candidate,safe="")}');resolved=(candidate,'/'.join(tail[count:]));break
            except Exception:
                if count>5:break
        if not resolved:raise RuntimeError('Referenced GitHub ref is unavailable: '+ '/'.join(tail))
        ref,path=resolved
    elif len(parts)>2 and parts[2]=='releases':
        if len(parts)>4 and parts[3]=='tag':ref='/'.join(parts[4:])
        elif len(parts)>3 and parts[3]=='latest':ref=get_json(f'https://api.github.com/repos/{owner}/{repo}/releases/latest')['tag_name']
    elif len(parts)>3 and parts[2]=='pull':ref=get_json(f'https://api.github.com/repos/{owner}/{repo}/pulls/{parts[3]}')['head']['sha']
    snapshot=archive_repo(owner,repo,ref)
    files=json.loads((ROOT/snapshot['file_index']).read_text())
    exists=not path or any(x['path']==path or x['path'].startswith(path.rstrip('/')+'/') for x in files)
    return {'status':'fetched' if exists else 'missing_scoped_path','kind':'github','scope_path':path,'scope_path_present':exists,**snapshot}

class PreParser(__import__('html.parser',fromlist=['HTMLParser']).HTMLParser):
    def __init__(self):super().__init__();self.depth=0;self.current=None;self.items=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='pre' and (re.match(r'editor\d*$',attrs.get('id','')) or 'js-sourcecopyarea' in attrs.get('class','')):self.current=[attrs,''];self.depth=1
        elif self.current:self.depth+=1
    def handle_endtag(self,tag):
        if self.current:
            self.depth-=1
            if self.depth==0:self.items.append(self.current);self.current=None
    def handle_data(self,data):
        if self.current:self.current[1]+=data

def parse_scan(text):
    for name in ('editor_contractJsonData','contractJsonData'):
        m=re.search(r"(?:var\s+)?"+name+r"\s*=\s*'((?:\\.|[^'\\])*)'",text)
        if m:
            raw=re.sub(r'\\([^"\\/bfnrtu])',r'\1',m.group(1))
            try:
                obj=json.loads(json.loads('"'+raw+'"'))
                if obj.get('sources'):return obj
            except (ValueError,TypeError):pass
    parser=PreParser();parser.feed(text)
    sources={}
    labels=re.findall(r'File\s+\d+\s+of\s+\d+\s*:\s*([^<]+)',text)
    for i,(attrs,content) in enumerate(parser.items):
        if not content.strip():continue
        if any(x in content for x in ('pragma solidity','SPDX-License-Identifier','contract ','interface ','library ','@version','def ')):
            label=html.unescape(labels[i].strip()) if i<len(labels) else 'Contract'+('' if i==0 else str(i))+('.vy' if '@version' in content else '.sol')
            sources[label]={'content':content}
    if sources:return {'sources':sources}
    return None

def persist_contract(entry, obj, kind, endpoint):
    dest=OUT/'contracts'/digest(entry['url']);dest.mkdir(parents=True,exist_ok=True)
    save_json(dest/'source.json',obj)
    return {'status':'fetched','kind':kind,'fetched_at':stamp(),'source':relative(dest/'source.json'),'retrieval_url':endpoint,'source_file_count':len(obj.get('sources',{}))}

def blockscout(entry, host, address):
    bases=['https://'+host]
    if host=='blockscout.com':
        path=urllib.parse.urlsplit(entry['url']).path.split('/address/')[0];bases=['https://'+host+path]
    errors=[]
    for base in bases:
        endpoint=base+'/api/v2/smart-contracts/'+address
        try:
            obj=get_json(endpoint)
            if obj.get('source_code'):
                obj['sources']={obj.get('file_path') or obj.get('name','Contract')+'.sol':{'content':obj['source_code']}}
                for extra in obj.get('additional_sources') or []:obj['sources'][extra.get('file_path') or extra.get('name','source.sol')]={'content':extra['source_code']}
                return persist_contract(entry,obj,'blockscout_verified_contract',endpoint)
            errors.append('API has no verified source')
        except Exception as e:errors.append(str(e))
    raise RuntimeError('; '.join(errors))

def stacks(entry):
    url=entry['url'];m=re.search(r'contract/([^/?#]+)',url)
    if not m:m=re.search(r'(S[PT][A-Z0-9]+\.[\w-]+)',url)
    if not m:raise RuntimeError('No Stacks contract identifier')
    contract=m.group(1);endpoint='https://api.hiro.so/extended/v1/contract/'+contract
    obj=get_json(endpoint)
    if not obj.get('source_code'):raise RuntimeError('Stacks API has no source')
    obj['sources']={contract+'.clar':{'content':obj['source_code']}}
    return persist_contract(entry,obj,'stacks_contract',endpoint)

def crate(entry):
    name=urllib.parse.urlsplit(entry['url']).path.strip('/').split('/')[1]
    data=get_json('https://crates.io/api/v1/crates/'+name);version=data['crate']['max_stable_version'] or data['crate']['max_version']
    dest=OUT/'crates'/safe(name)/safe(version);dest.mkdir(parents=True,exist_ok=True)
    path,_=download(f'https://crates.io/api/v1/crates/{name}/{version}/download',dest/'source.crate',timeout=180)
    with tarfile.open(path,'r:gz') as tf:count=len(tf.getmembers())
    save_json(dest/'metadata.json',data)
    return {'status':'fetched','kind':'rust_crate','version':version,'archive':relative(path),'file_count':count,'archive_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

def generic(entry):
    url=entry['url']; parsed=urllib.parse.urlsplit(url);host=parsed.netloc.lower(); address=re.search(r'0x[a-fA-F0-9]{40}',url)
    if 'hiro.so' in host:return stacks(entry)
    if host=='crates.io':return crate(entry)
    if address and any(s in host for s in ('blockscout','explorer.inkonchain','explorer.gobob','explorer.hemi','explorer.tac','explorer.lyra','explorer.mantle','explorer.intuition','explorer.plume','explorer.orderly','explorer.morph')):
        try:return blockscout(entry,host,address.group())
        except Exception:pass
    fetch_url=url.split('#')[0]
    if address and any(s in host for s in ('scan.','scan.org','scan.io','scan.xyz','snowtrace','hecoinfo')):fetch_url='https://'+host+'/address/'+address.group()+'#code'
    file,_=download(fetch_url);body=file.read_bytes();text=body.decode('utf-8','replace');file.unlink(missing_ok=True)
    obj=parse_scan(text)
    if obj:return persist_contract(entry,obj,'explorer_verified_contract',fetch_url)
    # Deployment explorer pages can refer to the verified implementation.
    if address:
        impl=[]
        for pattern in (r'Implementation[^\n]{0,1500}?(0x[a-fA-F0-9]{40})',r'implementationAddress["\x27]?\s*[:=]\s*["\x27](0x[a-fA-F0-9]{40})'):
            impl.extend(re.findall(pattern,text,re.I))
        impl=[x for x in dict.fromkeys(impl) if x.lower()!=address.group().lower()]
        for a in impl[:4]:
            try:
                f,_=download('https://'+host+'/address/'+a+'#code');o=parse_scan(f.read_text(errors='replace'));f.unlink(missing_ok=True)
                if o:
                    result=persist_contract(entry,o,'explorer_implementation_contract','https://'+host+'/address/'+a+'#code');result['implementation_address']=a;return result
            except Exception:pass
        if 'Contract Source Code Not Verified' in text or 'Are you the contract creator' in text:
            status='unverified_contract'
        else:status='source_unavailable'
    elif any(a.get('isPrimacyOfImpact') for a in entry.get('assets',[])) and host in ('immunefi.com','www.immunefi.com'):
        status='scope_placeholder'
    else:status='public_page_only'
    dest=OUT/'pages'/entry['id'];dest.mkdir(parents=True,exist_ok=True)
    with gzip.open(dest/'response.gz','wb') as g:g.write(body)
    result={'status':status,'kind':'public_response','response':relative(dest/'response.gz'),'retrieval_url':fetch_url,'fetched_at':stamp(),'response_sha256':hashlib.sha256(body).hexdigest()}
    # Public web assets: preserve browser-delivered first-party script sources as well.
    if not address and status=='public_page_only':
        scripts=[]
        for src in re.findall(r'<script\b[^>]*\bsrc=["\x27]([^"\x27]+)',text,re.I):
            target=urllib.parse.urljoin(fetch_url,html.unescape(src))
            if urllib.parse.urlsplit(target).netloc != parsed.netloc:continue
            scripts.append(target)
        bundles=[]
        for target in dict.fromkeys(scripts):
            try:
                f,_=download(target,retries=2,timeout=45);b=f.read_bytes();f.unlink(missing_ok=True);name=digest(target)+'.js.gz'
                with gzip.open(dest/name,'wb') as g:g.write(b)
                bundles.append({'url':target,'path':relative(dest/name),'sha256':hashlib.sha256(b).hexdigest()})
            except Exception as e:bundles.append({'url':target,'error':str(e)})
        if bundles:save_json(dest/'scripts.json',bundles);result['browser_scripts']=relative(dest/'scripts.json');result['script_count']=sum('path' in b for b in bundles)
    return result

def fetch(entry):
    start=time.time()
    try:
        host=urllib.parse.urlsplit(entry['url']).netloc.lower()
        result=github(entry) if host in ('github.com','www.github.com') else generic(entry)
    except Exception as e:result={'status':'failed','error':str(e),'fetched_at':stamp()}
    return {**entry,**result,'elapsed_seconds':round(time.time()-start,2)}

def push(message):
    subprocess.run(['git','add','--','sources','tools/fetch_sources.py','README.md','.gitignore'],cwd=ROOT,check=True)
    if subprocess.run(['git','diff','--cached','--quiet'],cwd=ROOT).returncode==0:return
    subprocess.run(['git','commit','-m',message],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
    p=subprocess.run(['git','push','origin','HEAD:main'],cwd=ROOT,capture_output=True,text=True)
    if p.returncode:
        # Preserve upstream automated metadata updates by replaying our additive commits.
        subprocess.run(['git','fetch','origin','main'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
        subprocess.run(['git','rebase','origin/main'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
        subprocess.run(['git','push','origin','HEAD:main'],cwd=ROOT,check=True)
    print('PUSHED',subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),flush=True)

def summarize(entries,results):
    from collections import Counter
    counts=dict(Counter(x['status'] for x in results.values()))
    summary={'updated_at':stamp(),'active_programs':len(json.loads((OUT/'metadata'/'selection.json').read_text())['active_programs']),'inventory_urls':len(entries),'processed_urls':len(results),'pending_urls':len(entries)-len(results),'statuses':counts}
    save_json(OUT/'summary.json',summary)
    save_json(OUT/'results.json',list(results.values()))
    print(json.dumps(summary),flush=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--workers',type=int,default=16);parser.add_argument('--retry',action='store_true');parser.add_argument('--kind',choices=['all','github','other'],default='all');parser.add_argument('--push',action='store_true');args=parser.parse_args()
    entries=inventory();results_path=OUT/'results.json';results={r['id']:r for r in json.loads(results_path.read_text())} if results_path.exists() else {}
    pending=[e for k,e in entries.items() if (k not in results or (args.retry and results[k]['status'] in ('failed','source_unavailable','missing_scoped_path'))) and (args.kind=='all' or (('github.com' in urllib.parse.urlsplit(e['url']).netloc)==(args.kind=='github')))]
    print('Fetching',len(pending),'URLs using',args.workers,'workers',flush=True)
    summarize(entries,results)
    if args.push:push('Initialize source inventory for metadata-active Immunefi programs')
    last=time.time();n=0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(fetch,e):e for e in pending}
        for future in concurrent.futures.as_completed(futures):
            r=future.result();results[r['id']]=r;n+=1
            print(f"{n}/{len(pending)} {r['status']} {r['url']}"+((' '+r['error']) if 'error' in r else ''),flush=True)
            if n%75==0 or time.time()-last>90:
                summarize(entries,results)
                if args.push:push(f'Archive active program sources: {len(results)}/{len(entries)} URLs processed')
                last=time.time()
    summarize(entries,results)
    if args.push:push(f'Archive active program sources: {len(results)}/{len(entries)} URLs processed')

if __name__=='__main__':main()
