#!/usr/bin/env python3
"""Triagem de repositórios Java com filtros ANTES da análise do código.

Pré-filtro: >= 1 estrela (CSV), push recente (API), commit mais recente
na branch padrão recente (API). Depois árvore Git e, finalmente, testes Java.
Usa poucas workers e espera/backoff para não sobrecarregar a API.
Não confunde indício sintático com aprovação científica.
Somente bibliotecas padrão Python.
"""
import argparse, base64, concurrent.futures, csv, datetime as dt, json, os, re, sys, threading, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

HEADERS = ['full_name','url','stars_csv','stars_atual','pushed_at','last_commit_date','last_commit_sha','api_status','triage_status','triage_reason','java_test_files_total','examined_files','files_with_gwt_tokens','files_with_jgiven_indicator','files_with_rest_assured_indicator','example_file_urls','scan_time_utc','manual_review']
lock = threading.Lock()
last_request_at = 0.0
stop = threading.Event()

def utc(s):
    return dt.datetime.fromisoformat(s.replace('Z','+00:00'))

def request(url, token='', json_mode=True, minimum_spacing=.28, retries=6):
    global last_request_at
    for retry in range(retries):
        if stop.is_set(): return None, 'STOPPED'
        with lock:
            wait = max(0, last_request_at + minimum_spacing - time.monotonic())
            if wait: time.sleep(wait)
            last_request_at = time.monotonic()
        headers={'Accept':'application/vnd.github+json' if json_mode else 'text/plain','User-Agent':'tcc-bdd-java-selection','X-GitHub-Api-Version':'2022-11-28'}
        if token and 'api.github.com' in url:headers['Authorization']='Bearer '+token
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=25) as response:
                data=response.read(2_000_000)
                return (json.loads(data) if json_mode else data.decode('utf-8',errors='replace')), 'OK'
        except urllib.error.HTTPError as e:
            if e.code==401:
                stop.set();return None,'HTTP_401_TOKEN_INVALIDO'
            if e.code in (404,409,451):return None,'HTTP_'+str(e.code)
            if e.code in (403,429):
                remaining=e.headers.get('X-RateLimit-Remaining')
                reset=e.headers.get('X-RateLimit-Reset')
                retry_after=e.headers.get('Retry-After')
                if remaining=='0' and reset:
                    delay=max(15,int(reset)-int(time.time())+5)
                else:
                    delay=max(60,int(retry_after or 0),min(600,60*(retry+1)))
                print(f'[LIMITE] HTTP {e.code}; aguardando {delay}s',flush=True)
                time.sleep(delay)
                continue
            if e.code>=500:
                time.sleep(min(30,2**retry));continue
            return None,'HTTP_'+str(e.code)
        except (OSError,TimeoutError,ValueError) as e:
            if retry==retries-1:return None,'ERRO_REDE_'+type(e).__name__
            time.sleep(min(30,2**retry))
    return None,'TENTATIVAS_ESGOTADAS'

def is_test(path):
    p='/'+path.lower().lstrip('/')
    return p.endswith('.java') and ('/src/test/' in p or '/test/' in p or '/tests/' in p) and not any(x in p for x in ('/target/','/build/','/generated/'))

def priority(path):
    p=path.lower()
    return 30*('jgiven' in p or 'bdd' in p or 'scenario' in p)+10*('acceptance' in p)+8*(p.endswith('test.java') or p.endswith('tests.java'))+5*('integration' in p)

def inspect(src):
    # Indícios no ARQUIVO, não comprovam ordem ou semântica no mesmo teste.
    g=bool(re.search(r'\b(?:given\s*\(|given\s*:|//\s*given\b|/\*\s*given\b)',src,re.I))
    w=bool(re.search(r'\b(?:when\s*\(|when\s*:|//\s*when\b|/\*\s*when\b)',src,re.I))
    t=bool(re.search(r'\b(?:then\s*\(|then\s*:|//\s*then\b|/\*\s*then\b)',src,re.I))
    j=bool(re.search(r'SimpleScenarioTest|ScenarioTest|com\.tngtech\.jgiven|@ScenarioStage|@Given\b|@When\b|@Then\b',src))
    ra=bool(re.search(r'io\.restassured|RestAssured|given\s*\(\s*\)\s*\.\s*(?:contentType|header|body|spec|auth)',src))
    test=bool(re.search(r'@Test\b|@ParameterizedTest\b|@RepeatedTest\b|extends\s+\w*ScenarioTest',src))
    return bool(g and w and t and test),j,ra

def scan(item, token, cutoff, max_files, spacing):
    name=item['full_name']; url=item.get('url') or 'https://github.com/'+name
    result={h:'' for h in HEADERS}
    result.update(full_name=name,url=url,stars_csv=item.get('stars',''),scan_time_utc=dt.datetime.now(dt.timezone.utc).isoformat(),manual_review='NECESSARIA')
    api=lambda endpoint:request('https://api.github.com/'+endpoint,token,minimum_spacing=spacing)
    meta,status=api('repos/'+name)
    if status!='OK':result.update(api_status=status,triage_status='ERRO',triage_reason='Falha ao consultar metadados');return result
    result['stars_atual']=meta.get('stargazers_count',0)
    if result['stars_atual']<1:result.update(api_status='OK',triage_status='EXCLUIDO_ESTRELAS',triage_reason='Repositório atualmente com zero estrelas');return result
    result['pushed_at']=meta.get('pushed_at','')
    try:recent_push=utc(meta['pushed_at'])>=cutoff
    except (KeyError,TypeError,ValueError):recent_push=False
    if not recent_push:result.update(api_status='OK',triage_status='EXCLUIDO_PUSH_ANTIGO',triage_reason='Sem push na branch/repositório no período');return result
    branch=meta.get('default_branch') or 'main'
    commit,status=api('repos/'+name+'/commits?per_page=1&sha='+urllib.parse.quote(branch,safe=''))
    if status!='OK' or not isinstance(commit,list) or not commit:
        result.update(api_status=status if status!='OK' else 'SEM_COMMITS',triage_status='ERRO',triage_reason='Não foi possível confirmar último commit na branch padrão');return result
    latest=commit[0]
    result['last_commit_date']=latest.get('commit',{}).get('committer',{}).get('date','')
    result['last_commit_sha']=latest.get('sha','')
    try:recent_commit=utc(result['last_commit_date'])>=cutoff
    except (TypeError,ValueError):recent_commit=False
    if not recent_commit:result.update(api_status='OK',triage_status='EXCLUIDO_COMMIT_ANTIGO',triage_reason='Último commit da branch padrão anterior ao corte');return result
    tree,status=api('repos/'+name+'/git/trees/'+urllib.parse.quote(branch,safe='')+'?recursive=1')
    if status!='OK' or not isinstance(tree,dict):
        result.update(api_status=status,triage_status='ERRO',triage_reason='Árvore Git indisponível');return result
    if tree.get('truncated'):
        result.update(api_status='TREE_TRUNCATED',triage_status='INCONCLUSIVO',triage_reason='Árvore truncada: inspeção incompleta');return result
    paths=[x for x in tree.get('tree',[]) if x.get('type')=='blob' and is_test(x.get('path',''))]
    paths.sort(key=lambda x:(-priority(x['path']),x['path']))
    result['java_test_files_total']=len(paths)
    if not paths:result.update(api_status='OK',triage_status='SEM_TESTES_JAVA',triage_reason='Nenhum Java em diretório convencional de testes');return result
    selected=paths if max_files==0 else paths[:max_files]
    matched=[];jgiven=[];rest=[];read=0
    for entry in selected:
        p=entry['path']
        raw='https://raw.githubusercontent.com/'+urllib.parse.quote(name,safe='/')+'/'+urllib.parse.quote(result['last_commit_sha'],safe='')+'/'+urllib.parse.quote(p,safe='/')
        src,raw_status=request(raw,json_mode=False,minimum_spacing=spacing)
        if raw_status!='OK':
            # Fallback com API para arquivos indisponíveis via raw (consome cota principal).
            blob,blob_status=api('repos/'+name+'/git/blobs/'+entry['sha'])
            if blob_status=='OK' and blob.get('encoding')=='base64':
                try:src=base64.b64decode(blob['content']).decode('utf-8',errors='replace')
                except (KeyError,ValueError):continue
            else:continue
        read+=1
        g,j,r=inspect(src)
        link='https://github.com/'+name+'/blob/'+result['last_commit_sha']+'/'+urllib.parse.quote(p,safe='/')
        if g:matched.append(link)
        if j:jgiven.append(link)
        if r:rest.append(link)
    result.update(api_status='OK',examined_files=read,files_with_gwt_tokens=len(matched),files_with_jgiven_indicator=len(jgiven),files_with_rest_assured_indicator=len(rest),example_file_urls=' | '.join(list(dict.fromkeys(matched+jgiven))[:5]))
    if matched or jgiven:result.update(triage_status='BDD_CANDIDATO',triage_reason='Indícios sintáticos em Java; confirmar comportamento real e natureza do projeto')
    elif read<len(paths):result.update(triage_status='INCONCLUSIVO',triage_reason='Não detectado na amostra; há arquivos ainda não examinados')
    else:result.update(triage_status='SEM_INDICIO_BDD',triage_reason='Nenhum indício nos arquivos examinados; sem exclusão científica automática')
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--input',default='triagem_integral_7203.csv')
    p.add_argument('--output',default='auditoria_bdd_filtrada.csv')
    p.add_argument('--excluded-output',default='exclusoes_bdd.csv')
    p.add_argument('--workers',type=int,default=3)
    p.add_argument('--min-stars',type=int,default=1)
    p.add_argument('--days',type=int,default=365)
    p.add_argument('--max-files',type=int,default=12,help='0=todos, recomendado primeira passagem: 12')
    p.add_argument('--max-repos',type=int,default=0,help='0=todos elegíveis ao filtro local')
    p.add_argument('--spacing',type=float,default=.28,help='Intervalo GLOBAL mínimo entre requisições (segundos)')
    p.add_argument('--previous',default='auditoria_codigo_resultados.csv',help='CSV anterior: reaproveita resultados OK quando válidos')
    args=p.parse_args()
    args.workers=max(1,min(4,args.workers));args.spacing=max(.2,args.spacing)
    token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if not token:sys.exit('Configure GH_TOKEN antes de executar. Nunca compartilhe o token.')
    base=Path(__file__).resolve().parent
    src=list(csv.DictReader(open(base/args.input,encoding='utf-8-sig',newline='')))
    if not src or 'full_name' not in src[0]:sys.exit('CSV sem coluna full_name')
    now=dt.datetime.now(dt.timezone.utc);cutoff=now-dt.timedelta(days=args.days)
    eligible=[];local_excluded=[]
    for row in src:
        try:stars=float(row.get('stars') or 0)
        except ValueError:stars=0
        if stars<args.min_stars:local_excluded.append([row['full_name'],row.get('url',''),'EXCLUIDO_ESTRELAS_CSV',f'estrelas CSV: {stars:g}'])
        else:eligible.append(row)
    if args.max_repos:eligible=eligible[:args.max_repos]
    print(f'[FILTRO LOCAL] {len(src)} registros; {len(local_excluded)} com < {args.min_stars} estrela(s); {len(eligible)} seguirão à API.',flush=True)
    print(f'[CRITÉRIOS] último commit na branch padrão desde {cutoff.date()}, {args.workers} workers, até {args.max_files or "TODOS"} testes/projeto.',flush=True)
    check,_=request('https://api.github.com/user',token,minimum_spacing=args.spacing)
    if not check or 'login' not in check:sys.exit('Falha na autenticação. Verifique GH_TOKEN.')
    print('[TOKEN] Autenticado como',check['login'],flush=True)
    out=base/args.output;excluded=base/args.excluded_output
    done=set()
    if out.exists():
        for r in csv.DictReader(open(out,encoding='utf-8-sig',newline='')):
            if r.get('api_status')=='OK' and r.get('triage_status') not in ('ERRO','INCONCLUSIVO'):done.add(r['full_name'])
    if excluded.exists():
        for r in csv.DictReader(open(excluded,encoding='utf-8-sig',newline='')):
            if r.get('reason') not in ('ERRO','INCONCLUSIVO'):done.add(r['full_name'])
    # Aproveita OK anterior somente quando data do último commit é verificável e recente.
    # Evita transferir um 'OK' sintático que ainda não atendeu aos filtros.
    if args.previous and (base/args.previous).exists():
        old={r['full_name']:r for r in csv.DictReader(open(base/args.previous,encoding='utf-8-sig',newline='')) if r.get('api_status')=='OK'}
        reused=[]
        for item in eligible:
            name=item['full_name'];r=old.get(name)
            if not r or name in done:continue
            try:recent=utc(r.get('last_commit_date',''))>=cutoff
            except (ValueError,TypeError):recent=False
            if not recent:continue
            d={h:'' for h in HEADERS}
            for k in ('full_name','url','last_commit_date','last_commit_sha','java_test_files_total','examined_files','files_with_gwt_tokens','files_with_jgiven_indicator','files_with_rest_assured_indicator','example_file_urls','scan_time_utc'):d[k]=r.get(k,'')
            d.update(stars_csv=item.get('stars',''),api_status='OK',triage_status='BDD_CANDIDATO' if int(r.get('files_with_gwt_tokens') or 0)+int(r.get('files_with_jgiven_indicator') or 0)>0 else 'INCONCLUSIVO',triage_reason='Reaproveitado de execução anterior; sem nova verificação de estrelas',manual_review='NECESSARIA')
            if d['triage_status']=='BDD_CANDIDATO':reused.append(d);done.add(name)
        if reused:
            with out.open('a',encoding='utf-8-sig' if not out.exists() else 'utf-8',newline='') as f:
                w=csv.DictWriter(f,fieldnames=HEADERS)
                if f.tell()==0:w.writeheader()
                w.writerows(reused)
            print(f'[RETOMADA] {len(reused)} registros antigos candidatos preservados.',flush=True)
    eligible=[r for r in eligible if r['full_name'] not in done]
    with out.open('a',encoding='utf-8-sig' if not out.exists() else 'utf-8',newline='') as fo, excluded.open('a',encoding='utf-8-sig' if not excluded.exists() else 'utf-8',newline='') as fe:
        wo=csv.DictWriter(fo,fieldnames=HEADERS);we=csv.writer(fe)
        if fo.tell()==0:wo.writeheader()
        if fe.tell()==0:we.writerow(['full_name','url','reason','detail'])
        # Local exclusions are written only once (repeated runs won't multiply them).
        already=set(done)
        for name,url,reason,detail in local_excluded:
            if name not in already:we.writerow([name,url,reason,detail]);already.add(name)
        fe.flush()
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures={pool.submit(scan,r,token,cutoff,args.max_files,args.spacing):r for r in eligible}
            for i,future in enumerate(concurrent.futures.as_completed(futures),1):
                row=futures[future]
                try:res=future.result()
                except Exception as e:
                    res={h:'' for h in HEADERS};res.update(full_name=row['full_name'],url=row.get('url',''),api_status='ERRO_INTERNO',triage_status='ERRO',triage_reason=repr(e)[:180])
                if res['triage_status'].startswith('EXCLUIDO'):
                    we.writerow([res['full_name'],res['url'],res['triage_status'],res['triage_reason']]);fe.flush()
                else:
                    wo.writerow(res);fo.flush()
                if i<=10 or i%10==0:print(f'[{i}/{len(eligible)}] {res["full_name"]}: {res["triage_status"]} | {res.get("examined_files",0)} arquivos',flush=True)
                if stop.is_set():print('[PARADO] Token inválido; revise GH_TOKEN.',flush=True);break
    print('[CONCLUÍDO] Candidatos:',out,'| exclusões:',excluded,flush=True)

if __name__=='__main__':main()
