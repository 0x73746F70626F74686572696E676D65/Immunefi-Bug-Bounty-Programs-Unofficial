#!/usr/bin/env python3
"""Archive public implementation source linked from verified proxy deployments."""
import concurrent.futures, json, re, urllib.parse
PRIMARY_INDEX={}
import fetch_sources as f

RESULTS=f.OUT/'proxy-sources.json'

def implementation_url(parent,address):
    parsed=urllib.parse.urlsplit(parent['url'])
    path=re.sub(r'0x[a-fA-F0-9]{40}(?![a-fA-F0-9])',address,parsed.path,count=1)
    if path==parsed.path:path='/address/'+address
    return urllib.parse.urlunsplit(('https',parsed.netloc,path,'','code'))

def network_key(url):
    parsed=urllib.parse.urlsplit(url)
    parts=parsed.path.strip('/').split('/')
    return (parsed.netloc,'/'.join(parts[:2]) if parsed.netloc=='blockscout.com' else parts[0] if parsed.netloc=='www.oklink.com' else '')

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
    if not addresses and re.search(r'contract\s+\w*Proxy\b|contract\s+Unitroller\b',text):
        parsed=urllib.parse.urlsplit(result['url']);match=re.search(r'0x[a-fA-F0-9]{40}(?![a-fA-F0-9])',result['url']);chain=f.CHAIN_IDS.get(parsed.netloc)
        if parsed.netloc=='www.oklink.com':chain={'x-layer':196,'xlayer':196}.get(parsed.path.strip('/').split('/')[0])
        endpoint=f.RPC_ENDPOINTS.get(chain)
        if endpoint and match:
            try:
                with f.RPC_GUARD:lock=f.RPC_LOCKS.setdefault(endpoint,__import__('threading').Lock())
                with lock:
                    if endpoint not in f.RPC_VERIFIED:
                        try:
                            observed=f.get_json(endpoint,post_json={'jsonrpc':'2.0','id':1,'method':'eth_chainId','params':[]}).get('result','0x0')
                            f.RPC_VERIFIED[endpoint]=int(observed,16)==chain
                        except Exception:f.RPC_VERIFIED[endpoint]=False
                if not f.RPC_VERIFIED[endpoint]:raise RuntimeError('RPC network could not be validated')
                slot='0x2' if re.search(r'contract\s+Unitroller\b',text) else '0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc'
                value=f.get_json(endpoint,post_json={'jsonrpc':'2.0','id':2,'method':'eth_getStorageAt','params':[match.group(),slot,'latest']}).get('result','0x0')
                if int(value,16):addresses.append('0x'+value[-40:])
                elif 'BeaconProxy' in text:
                    value=f.get_json(endpoint,post_json={'jsonrpc':'2.0','id':3,'method':'eth_getStorageAt','params':[match.group(),'0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50','latest']}).get('result','0x0')
                    if int(value,16):
                        beacon='0x'+value[-40:];value=f.get_json(endpoint,post_json={'jsonrpc':'2.0','id':4,'method':'eth_call','params':[{'to':beacon,'data':'0x5c60da1b'},'latest']}).get('result','0x0')
                        if int(value,16):addresses.append('0x'+value[-40:])
            except Exception:pass
    return list(dict.fromkeys(a.lower() for a in addresses))

def fetch_impl(parent,address):
    host=urllib.parse.urlsplit(parent['url']).netloc
    url=implementation_url(parent,address)
    entry={'id':f.digest(url),'url':url,'programs':parent.get('programs',[]),'assets':[{'type':'smart_contract','description':'Proxy implementation for '+parent['url']}],'metadata_fields':[]}
    cached=PRIMARY_INDEX.get((network_key(url),address.lower()))
    result={**cached,**entry,'shared_source_from':cached['url']} if cached else f.fetch(entry)
    result['parent_url']=parent['url'];result['implementation_address']=address
    return result

def main(retry_failed=False):
    results={r['id']:r for r in json.loads(RESULTS.read_text())} if RESULTS.exists() else {}
    recovery_file=f.OUT/'proxy-recoveries.json'
    if recovery_file.exists():
        for row in json.loads(recovery_file.read_text()):
            if row.get('status')=='fetched' and results.get(row['id'],{}).get('status')!='fetched':results[row['id']]=row
    primary=json.loads((f.OUT/'results.json').read_text())
    for row in primary:
        if row.get('status')!='fetched' or not row.get('source'):continue
        match=re.search(r'0x[a-fA-F0-9]{40}(?![a-fA-F0-9])',row['url'])
        if match:PRIMARY_INDEX[(network_key(row['url']),match.group().lower())]=row
    if retry_failed:
        for key,row in list(results.items()):
            if row['status']=='fetched' or not row.get('parent_url'):continue
            corrected=implementation_url({'url':row['parent_url']},row['implementation_address'])
            if corrected!=row['url']:del results[key]
    parents=[r for r in primary if r.get('source')]+[r for r in results.values() if r.get('source')]
    seen=set();round_number=0
    while parents:
        round_number+=1;tasks=[]
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            discovered=pool.map(discover,parents)
            for parent,addresses in zip(parents,discovered):
                host=urllib.parse.urlsplit(parent['url']).netloc
                for address in addresses:
                    key=f.digest(implementation_url(parent,address))
                    if key in seen or (key in results and (not retry_failed or results[key]['status']=='fetched')):continue
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
