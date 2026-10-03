#!/usr/bin/env python3
"""Archive public implementation source linked from verified proxy deployments."""
import concurrent.futures, json, re, urllib.parse
import fetch_sources as f

RESULTS=f.OUT/'proxy-sources.json'

def discover(result):
    path=f.ROOT/result['source'];obj=json.loads(path.read_text());addresses=list(obj.get('explorer_implementation_addresses') or [])
    for key in ('implementations',):
        for impl in obj.get(key) or []:
            if isinstance(impl,dict):
                a=impl.get('address') or impl.get('address_hash')
                if a:addresses.append(a)
    for impl in (obj.get('proxyResolution') or {}).get('implementations',[]):
        if impl.get('address'):addresses.append(impl['address'])
    for a in obj.get('implementation_sources') or {}:addresses.append(a)
    text='\n'.join(v.get('content','') for v in obj.get('sources',{}).values() if isinstance(v,dict))
    if not addresses and result.get('kind') in ('explorer_verified_contract','explorer_implementation_contract') and re.search(r'contract\s+\w*Proxy\b|contract\s+Unitroller\b',text):
        try:
            page,_=f.download(result.get('retrieval_url') or result['url']);addresses=f.proxy_addresses(page.read_text(errors='replace'));page.unlink(missing_ok=True)
        except Exception:pass
    return list(dict.fromkeys(a.lower() for a in addresses))

def fetch_impl(parent,address):
    host=urllib.parse.urlsplit(parent['url']).netloc
    url='https://'+host+'/address/'+address+'#code'
    entry={'id':f.digest(url),'url':url,'programs':parent.get('programs',[]),'assets':[{'type':'smart_contract','description':'Proxy implementation for '+parent['url']}],'metadata_fields':[]}
    result=f.fetch(entry);result['parent_url']=parent['url'];result['implementation_address']=address
    return result

def main():
    results={r['id']:r for r in json.loads(RESULTS.read_text())} if RESULTS.exists() else {}
    parents=[r for r in json.loads((f.OUT/'results.json').read_text()) if r.get('source')]+[r for r in results.values() if r.get('source')]
    seen=set();round_number=0
    while parents:
        round_number+=1;tasks=[]
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            discovered=pool.map(discover,parents)
            for parent,addresses in zip(parents,discovered):
                host=urllib.parse.urlsplit(parent['url']).netloc
                for address in addresses:
                    key=f.digest('https://'+host+'/address/'+address+'#code')
                    if key in seen or key in results:continue
                    if address in parent['url'].lower():continue
                    seen.add(key);tasks.append((parent,address))
        print('Proxy source round',round_number,'implementations',len(tasks),flush=True);parents=[]
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            futures=[pool.submit(fetch_impl,*t) for t in tasks]
            for future in concurrent.futures.as_completed(futures):
                result=future.result();results[result['id']]=result
                if result.get('source'):parents.append(result)
                print(result['status'],result['url'],flush=True)
                if len(results)%75==0:
                    f.save_json(RESULTS,list(results.values()));f.push(f'Archive proxy implementation source: {len(results)} deployments processed')
        f.save_json(RESULTS,list(results.values()));f.push(f'Archive proxy implementation source: {len(results)} deployments processed')
    print('Proxy implementation results',len(results),flush=True)

if __name__=='__main__':main()
