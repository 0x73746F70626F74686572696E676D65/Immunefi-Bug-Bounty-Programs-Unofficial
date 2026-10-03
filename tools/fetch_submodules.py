#!/usr/bin/env python3
"""Resolve and archive the pinned Git submodules of downloaded source snapshots."""
import concurrent.futures, configparser, json, pathlib, subprocess, urllib.parse
import fetch_sources as f

def resolve_module(parent, path, configured_url):
    owner,repo=urllib.parse.urlsplit(parent['repository']).path.strip('/').split('/')[:2]
    endpoint=f'https://api.github.com/repos/{owner}/{repo}/contents/{urllib.parse.quote(path,safe="/")}?ref={parent["commit"]}'
    obj=f.get_json(endpoint);commit=obj['sha'];url=obj.get('submodule_git_url') or configured_url
    if url.startswith(('./','../')):url=urllib.parse.urljoin(parent['repository']+'.git/',url)
    if url.startswith('git@github.com:'):url='https://github.com/'+url[len('git@github.com:'):]
    if url.startswith('ssh://git@github.com/'):url='https://github.com/'+url[len('ssh://git@github.com/'):]
    p=urllib.parse.urlsplit(url)
    if p.netloc=='github.com':
        parts=p.path.strip('/').removesuffix('.git').split('/')
        snapshot=f.archive_repo(parts[0],parts[1],commit)
    elif p.scheme in ('http','https'):
        cache=f.WORK/('module-'+f.digest(url+commit));cache.mkdir(exist_ok=True)
        subprocess.run(['git','init','--bare',str(cache)],check=True,capture_output=True)
        proc=subprocess.run(['git','-C',str(cache),'fetch','--depth=1',url,commit],capture_output=True,text=True,timeout=180)
        if proc.returncode:raise RuntimeError(proc.stderr[-500:])
        dest=f.OUT/'git-submodules'/f.digest(url)/commit;dest.mkdir(parents=True,exist_ok=True)
        tmp=f.temporary('module-');subprocess.run(['git','-C',str(cache),'archive','--format=tar.gz','--output',str(tmp),commit],check=True,capture_output=True)
        tmp.replace(dest/'source.tar.gz')
        snapshot={'repository':url,'commit':commit,'archive':f.relative(dest/'source.tar.gz')}
    else:raise RuntimeError('Unsupported submodule transport: '+url)
    return {'status':'fetched','parent_snapshot':parent['snapshot'],'path':path,'configured_url':configured_url,'resolved_url':url,'commit':commit,'source_snapshot':snapshot}

def tasks():
    result={}
    for file in (f.OUT/'github').rglob('snapshot.json'):
        parent=json.loads(file.read_text());text=parent.get('submodule_configuration')
        if not text:continue
        config=configparser.ConfigParser(interpolation=None,strict=False)
        try:config.read_string(text)
        except configparser.Error:continue
        for section in config.sections():
            if not config.has_option(section,'path') or not config.has_option(section,'url'):continue
            path=config.get(section,'path').strip('"');url=config.get(section,'url').strip('"')
            key=f.digest(parent['snapshot']+'|'+path);result[key]=(parent,path,url)
    return result

def main():
    results_file=f.OUT/'submodules.json';results={r['id']:r for r in json.loads(results_file.read_text())} if results_file.exists() else {}
    while True:
        todo=[(k,t) for k,t in tasks().items() if k not in results]
        if not todo:break
        print('Submodules remaining',len(todo),flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            futures={pool.submit(resolve_module,*task):(key,task) for key,task in todo}
            for future in concurrent.futures.as_completed(futures):
                key,task=futures[future]
                try:r=future.result()
                except Exception as e:r={'status':'failed','parent_snapshot':task[0]['snapshot'],'path':task[1],'configured_url':task[2],'error':str(e)}
                r['id']=key;results[key]=r;print(r['status'],r['configured_url'],r.get('error',''),flush=True)
                if len(results)%40==0:
                    f.save_json(results_file,list(results.values()));f.push(f'Archive pinned source submodules: {len(results)} resolved')
        f.save_json(results_file,list(results.values()));f.push(f'Archive pinned source submodules: {len(results)} resolved')
    print('Submodule results',len(results),flush=True)

if __name__=='__main__':main()
