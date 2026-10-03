#!/usr/bin/env python3
"""Check archive integrity and report source coverage without hiding failed URLs."""
import collections, hashlib, json, pathlib
import fetch_sources as f

def main():
    inventory=json.loads((f.OUT/'inventory.json').read_text());results=json.loads((f.OUT/'results.json').read_text());by_id={r['id']:r for r in results}
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
    pending=[e for e in inventory if e['id'] not in by_id]
    unresolved=[r for r in results if r['status'] in ('failed','partial','source_unavailable','missing_scoped_path','unverified_contract')]
    selection=json.loads((f.OUT/'metadata'/'selection.json').read_text());coverage={}
    for slug in selection['active_programs']:
        urls=[e for e in inventory if slug in e['programs']]
        coverage[slug]={'inventory_urls':len(urls),'statuses':dict(collections.Counter(by_id[e['id']]['status'] if e['id'] in by_id else 'pending' for e in urls))}
    f.save_json(f.OUT/'coverage.json',coverage);f.save_json(f.OUT/'unresolved.json',unresolved)
    report={'verified_at':f.stamp(),'active_programs':len(selection['active_programs']),'inventory_urls':len(inventory),'processed_urls':len(results),'pending_urls':len(pending),'unresolved_urls':len(unresolved),'repository_snapshots_checked':archives,'archive_bytes_checked':total_bytes,'contract_source_records_checked':contracts,'contract_source_files':source_files,'integrity_errors':errors,'all_inventory_urls_attempted':not pending,'all_inventory_urls_have_source':not pending and not unresolved}
    f.save_json(f.OUT/'verification.json',report);print(json.dumps(report),flush=True);f.push('Verify archived source integrity and record active-program coverage')
    if errors or pending:raise SystemExit(1)

if __name__=='__main__':main()
