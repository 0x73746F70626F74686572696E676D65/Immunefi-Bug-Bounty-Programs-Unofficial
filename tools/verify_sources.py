#!/usr/bin/env python3
"""Check archive integrity and report source coverage without hiding failed URLs."""
import collections, gzip, hashlib, json, pathlib, subprocess
import fetch_sources as f
import fetch_submodules as submodules
import fetch_proxy_sources as proxies

def main():
    submodules.main(retry_failed=True)
    proxies.main(retry_failed=True)
    inventory=json.loads((f.OUT/'inventory.json').read_text());results=json.loads((f.OUT/'results.json').read_text())
    repaired=[]
    for row in results:
        if row['status']!='missing_scoped_path' or row['url']!='https://github.com/aave-dao/gho-origin/blob/main/src/contracts/facilitators/aave/oracle/GhoOracle.so':continue
        path='src/contracts/misc/GhoOracle.sol'
        if any(item['path']==path for item in json.loads((f.ROOT/row['file_index']).read_text())):
            row.update(status='fetched',scope_path=path,scope_path_present=True,metadata_url_repair={'original_url':row['url'],'resolved_url':'https://github.com/aave-dao/gho-origin/blob/main/'+path,'reason':'The pinned archive contains the requested GhoOracle source under its current directory with the full .sol extension.'});repaired.append(row)
    if repaired:
        f.save_json(f.OUT/'results.json',results)
        summary=json.loads((f.OUT/'summary.json').read_text());summary['statuses']=dict(collections.Counter(r['status'] for r in results));f.save_json(f.OUT/'summary.json',summary)
        manifest=f.OUT/'recoveries.json';recovered={r['id']:r for r in json.loads(manifest.read_text())} if manifest.exists() else {}
        recovered.update({r['id']:r for r in repaired});f.save_json(manifest,list(recovered.values()))
    by_id={r['id']:r for r in results}
    errors=[];archives=0;total_bytes=0
    for file in (f.OUT/'github').rglob('snapshot.json'):
        obj=json.loads(file.read_text());digest=hashlib.sha256()
        try:
            for part in obj['archive_parts']:
                path=f.ROOT/part
                with path.open('rb') as stream:
                    while block:=stream.read(1024*1024):digest.update(block);total_bytes+=len(block)
            if digest.hexdigest()!=obj['archive_sha256']:errors.append({'snapshot':f.relative(file),'error':'Archive SHA-256 mismatch'})
            for path in (obj['file_index'],):
                if not (f.ROOT/path).is_file():errors.append({'snapshot':f.relative(file),'error':'File index is missing'})
            archives+=1
        except Exception as e:errors.append({'snapshot':f.relative(file),'error':str(e)})
    contracts=0;source_files=0
    for file in (f.OUT/'contracts').glob('*/source.json'):
        obj=json.loads(file.read_text());sources=obj.get('sources') or {}
        if not sources:errors.append({'source':f.relative(file),'error':'No source files'})
        for name,value in sources.items():
            if not isinstance(value,dict) or not isinstance(value.get('content'),str):errors.append({'source':f.relative(file),'error':'Invalid source content for '+name})
        contracts+=1;source_files+=len(sources)
    pages=0;crates=0;browser_scripts=0
    tracked=set(subprocess.check_output(['git','ls-files','-z','--','sources'],cwd=f.ROOT).decode().split('\0'))
    for result in results:
        try:
            if result.get('response'):
                path=f.ROOT/result['response'];body=gzip.decompress(path.read_bytes())
                if hashlib.sha256(body).hexdigest()!=result['response_sha256']:errors.append({'source':result['response'],'error':'Public response SHA-256 mismatch'})
                pages+=1
            if result.get('kind')=='rust_crate':
                path=f.ROOT/result['archive']
                if hashlib.sha256(path.read_bytes()).hexdigest()!=result['archive_sha256']:errors.append({'source':result['archive'],'error':'Crate SHA-256 mismatch'})
                crates+=1
            if result.get('browser_scripts'):
                for script in json.loads((f.ROOT/result['browser_scripts']).read_text()):
                    if not script.get('path'):continue
                    body=gzip.decompress((f.ROOT/script['path']).read_bytes())
                    if hashlib.sha256(body).hexdigest()!=script['sha256']:errors.append({'source':script['path'],'error':'Browser script SHA-256 mismatch'})
                    browser_scripts+=1
        except Exception as e:errors.append({'url':result['url'],'error':str(e)})
    untracked=[f.relative(path) for path in f.OUT.rglob('*') if path.is_file() and f.relative(path) not in tracked]
    if untracked:errors.append({'error':'Source files missing from Git tracking','paths':untracked})
    pending=[e for e in inventory if e['id'] not in by_id]
    unresolved=[r for r in results if r['status'] in ('failed','partial','source_unavailable','missing_scoped_path','unverified_contract')]
    auxiliary={}
    for name in ('submodules','proxy-sources'):
        manifest=f.OUT/(name+'.json')
        records=json.loads(manifest.read_text()) if manifest.exists() else []
        auxiliary[name]={'records':len(records),'statuses':dict(collections.Counter(r['status'] for r in records)),'unresolved':[r for r in records if r['status'] not in ('fetched','not_a_submodule')]}
    source_less=[r for r in results if r['status'] not in ('fetched','not_a_submodule')]
    selection=json.loads((f.OUT/'metadata'/'selection.json').read_text());coverage={}
    for slug in selection['active_programs']:
        urls=[e for e in inventory if slug in e['programs']]
        coverage[slug]={'inventory_urls':len(urls),'statuses':dict(collections.Counter(by_id[e['id']]['status'] if e['id'] in by_id else 'pending' for e in urls))}
    f.save_json(f.OUT/'coverage.json',coverage);f.save_json(f.OUT/'unresolved.json',unresolved)
    report={'verified_at':f.stamp(),'active_programs':len(selection['active_programs']),'inventory_urls':len(inventory),'processed_urls':len(results),'pending_urls':len(pending),'unresolved_urls':len(unresolved),'repository_snapshots_checked':archives,'archive_bytes_checked':total_bytes,'contract_source_records_checked':contracts,'contract_source_files':source_files,'public_responses_checked':pages,'rust_crates_checked':crates,'browser_scripts_checked':browser_scripts,'all_existing_sources_tracked':not untracked,'integrity_errors':errors,'all_inventory_urls_attempted':not pending,'statuses':dict(collections.Counter(r['status'] for r in results)),'urls_without_source':len(source_less),'auxiliary_sources':auxiliary,'all_inventory_urls_have_source':not pending and not source_less}
    f.save_json(f.OUT/'verification.json',report);print(json.dumps(report),flush=True);f.push('Verify archived source integrity and record active-program coverage')
    if errors or pending:raise SystemExit(1)

if __name__=='__main__':main()
