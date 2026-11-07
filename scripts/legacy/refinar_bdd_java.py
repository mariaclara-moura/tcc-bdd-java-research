#!/usr/bin/env python3
"""Segunda passagem de triagem BDD-Java para o TCC.

Entrada: auditoria_bdd_filtrada.csv (312 projetos que já passaram nos filtros).
Reutiliza SHA da primeira coleta; não repete consulta de estrelas/commits.
Percorre árvores inteiras, amplia diretórios de testes e avalia evidência dentro
DOS METODOS DE TESTE (aproximadamente, sem parser Java), distinguindo JGiven,
comentários GWT e Rest Assured. Nenhum resultado é aprovação científica.

Python >=3.9, biblioteca padrão. Resumo e CSVs incrementais, sem sobrescrever
os arquivos originais. Execução retomável (somente OK/completo é pulado).
"""
import argparse
import concurrent.futures
import csv
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

COLUMNS = [
    'full_name','url','old_status','last_commit_sha','last_commit_date',
    'api_status','new_status','decision_reason','tree_truncated',
    'java_test_files_found','files_examined','files_failed',
    'gwt_test_methods','jgiven_test_methods','comment_gwt_test_methods',
    'rest_assured_only_methods','other_gwt_methods',
    'files_with_positive_evidence','evidence_kind','evidence_urls',
    'evidence_details','possible_demo_or_framework','manual_review',
    'completed_scan','scan_timestamp_utc'
]
EXCLUDED_PARTS = ('/target/','/build/','/out/','/.gradle/','/node_modules/',
                  '/generated/','/generated-sources/','/vendor/','/dist/')
METHOD_ANNOTATION = re.compile(r'@(?:org\.junit\.[\w.]+\.)?(?:Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate)\b|@org\.testng\.annotations\.Test\b|@Test\b', re.I)
METHOD_NAME = re.compile(r'\b(?:public|protected|private|static|final|synchronized|\s)*\b(?:void|[\w<>\[\],?]+)\s+(\w+)\s*\([^;{}]*\)\s*(?:throws\s+[\w., ]+)?\s*\{', re.S)
G = re.compile(r'(?:\bgiven\s*\(|\bgiven\s*:|\bgiven\s*\.|//\s*given\b|/\*+\s*given\b)',re.I)
W = re.compile(r'(?:\bwhen\s*\(|\bwhen\s*:|\bwhen\s*\.|//\s*when\b|/\*+\s*when\b)',re.I)
T = re.compile(r'(?:\bthen\s*\(|\bthen\s*:|\bthen\s*\.|//\s*then\b|/\*+\s*then\b)',re.I)
JGIVEN = re.compile(r'com\.tngtech\.jgiven|SimpleScenarioTest|\bScenarioTest\s*<|@ScenarioStage\b|\bScenarioStage\b')
REST = re.compile(r'io\.restassured|RestAssured|RequestSpecification|ResponseSpecification|\bget\s*\(\s*"/',re.I)
DEMO = re.compile(r'\b(?:demo|tutorial|kata|workshop|training|exercises?|starter|boilerplate|example|sample|sandbox|learning|course)\b',re.I)
FRAMEWORK = re.compile(r'\b(?:jgiven|cucumber|serenity|test-framework|bdd-framework|testing-framework)\b',re.I)

class Throttle:
    def __init__(self,spacing):
        self.spacing=spacing; self.lock=threading.Lock();self.last=0.0
    def wait(self):
        with self.lock:
            d=self.last+self.spacing-time.monotonic()
            if d>0:time.sleep(d)
            self.last=time.monotonic()

def get(url,token,throttle,is_json=True,retries=5,max_bytes=3_000_000):
    for attempt in range(retries):
        throttle.wait()
        h={'User-Agent':'tcc-code-style-bdd-audit','Accept':'application/vnd.github+json' if is_json else 'text/plain'}
        if 'api.github.com' in url and token: h['Authorization']='Bearer '+token
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers=h),timeout=35) as resp:
                raw=resp.read(max_bytes+1)
                if len(raw)>max_bytes:return None,'TOO_LARGE'
                return (json.loads(raw) if is_json else raw.decode('utf-8','replace')),'OK'
        except urllib.error.HTTPError as err:
            if err.code==401:return None,'HTTP_401'
            if err.code in (403,429):
                remaining=err.headers.get('X-RateLimit-Remaining')
                reset=err.headers.get('X-RateLimit-Reset')
                retry_after=err.headers.get('Retry-After')
                if remaining=='0' and reset:
                    delay=max(5,int(reset)-int(time.time())+4)
                else:
                    delay=min(180,max(30,30*(attempt+1),int(retry_after or 0)))
                print(f'[API] HTTP {err.code}: aguardando {delay}s; remaining={remaining}',flush=True)
                time.sleep(delay);continue
            if err.code>=500:time.sleep(min(30,2**attempt));continue
            return None,'HTTP_'+str(err.code)
        except (TimeoutError,OSError,ValueError) as err:
            if attempt==retries-1:return None,'NETWORK_'+type(err).__name__
            time.sleep(min(15,2**attempt))
    return None,'RETRY_EXHAUSTED'

def test_path(path):
    p='/'+path.replace('\\','/').lower().lstrip('/')
    if not p.endswith('.java') or any(s in p for s in EXCLUDED_PARTS):return False
    base=p.rsplit('/',1)[-1]
    # pastas tradicionais e alternativas
    folder=bool(re.search(r'/(?:test|tests|tst|src/it|src/integrationtest|integrationtest|integration-tests|integrationtests|acceptance-tests|acceptancetest|functionaltest|functional-tests|src/testfixtures)/',p))
    # sufixo típico mesmo quando localizado em pasta incomum
    suffix=bool(re.search(r'(?:test|tests|testcase|testcases|it|spec|specification|scenario|scenariotest)\.java$',base))
    # 'StepDefinitions.java', classes de stages, suites em pastas de teste
    stage=folder and bool(re.search(r'(?:stage|stepdefinition|steps|spec)\.java$',base))
    return folder or suffix or stage

def order_score(path):
    p=path.lower();score=0
    for x,s in [('jgiven',35),('scenario',25),('bdd',25),('behaviour',18),('behavior',18),('acceptance',12),('test',5)]:
        if x in p:score+=s
    if re.search(r'(test|tests|spec|it)\.java$',p):score+=10
    if 'stepdefinition' in p:score-=18 # pistas de Cucumber, não prioridade
    return score

def masked_source(code):
    # Remover comentários de linha (mas preservá-los para análise de GWT).
    # Deixar o código original para verificar comentários de separação de etapas.
    return code

def method_bodies(src):
    """Extrai blocos próximos a @Test por balanceamento de chaves.
    Heurística: NÃO constitui parser Java; não valida semântica.
    """
    found=[]
    for ann in METHOD_ANNOTATION.finditer(src):
        header=src[ann.end():ann.end()+1100]
        m=METHOD_NAME.search(header)
        if not m:continue
        start=ann.end()+m.end()-1
        depth=0;end=None
        for i in range(start,min(len(src),start+250_000)):
            if src[i]=='{':depth+=1
            elif src[i]=='}':
                depth-=1
                if depth==0:end=i+1;break
        if end is not None:found.append((m.group(1),src[start:end]))
    return found

def analyze_java(src):
    jgiven=bool(JGIVEN.search(src)); rest=bool(REST.search(src))
    counts=dict(gwt=0,jgiven=0,comments=0,rest_only=0,other=0)
    samples=[]
    for method,body in method_bodies(src):
        gm=G.search(body);wm=W.search(body);tm=T.search(body)
        if not (gm and wm and tm):continue
        if not (gm.start()<wm.start()<tm.start()):continue
        counts['gwt']+=1
        comment=bool(re.search(r'//\s*given\b',body,re.I) and re.search(r'//\s*when\b',body,re.I) and re.search(r'//\s*then\b',body,re.I))
        jgiven_calls=bool(re.search(r'\bgiven\s*\(\s*\)\s*\.',body,re.I) and re.search(r'\bwhen\s*\(\s*\)\s*\.',body,re.I) and re.search(r'\bthen\s*\(\s*\)\s*\.',body,re.I))
        if jgiven and jgiven_calls:
            k='JGiven';counts['jgiven']+=1
        elif comment:
            k='Comentarios_GWT';counts['comments']+=1
        elif rest:
            k='REST_Assured_ou_HTTP';counts['rest_only']+=1
        else:
            k='GWT_Outro';counts['other']+=1
        if len(samples)<6:samples.append(f'{method}:{k}')
    return counts,samples

def scan(row,args,throttle):
    name=row['full_name'].strip()
    base={k:'' for k in COLUMNS}
    base.update(full_name=name,url=row.get('url') or 'https://github.com/'+name,
                old_status=row.get('triage_status',''),last_commit_sha=row.get('last_commit_sha',''),
                last_commit_date=row.get('last_commit_date',''),manual_review='NECESSARIA',
                scan_timestamp_utc=dt.datetime.now(dt.timezone.utc).isoformat())
    # Segurança: o CSV precisa ter um SHA testado na primeira execução; quando faltante,
    # recupera a branch e o último commit apenas para o repositório com erro.
    sha=base['last_commit_sha']
    api=lambda suffix:get('https://api.github.com/'+suffix,args.token,throttle)
    if not sha:
        meta,st=api('repos/'+name)
        if st!='OK':base.update(api_status=st,new_status='ERRO',decision_reason='Falha ao recuperar metadados');return base
        branch=meta.get('default_branch','main')
        commit,st=api('repos/'+name+'/commits?per_page=1&sha='+urllib.parse.quote(branch,safe=''))
        if st!='OK' or not commit:
            base.update(api_status=st,new_status='ERRO',decision_reason='Falha ao recuperar commit');return base
        sha=commit[0]['sha'];base['last_commit_sha']=sha
        base['last_commit_date']=commit[0].get('commit',{}).get('committer',{}).get('date','')
    tree,st=api('repos/'+name+'/git/trees/'+urllib.parse.quote(sha,safe='')+'?recursive=1')
    if st!='OK' or not isinstance(tree,dict):
        base.update(api_status=st,new_status='ERRO',decision_reason='Erro na árvore Git');return base
    truncated=bool(tree.get('truncated'));base['tree_truncated']=str(truncated)
    entries=[e for e in tree.get('tree',[]) if e.get('type')=='blob' and test_path(e.get('path',''))]
    entries.sort(key=lambda e:(-order_score(e['path']),e['path']))
    base['java_test_files_found']=len(entries)
    if not entries:
        base.update(api_status='OK',new_status='SEM_TESTES_RECONHECIDOS',completed_scan=str(not truncated),
                    decision_reason='Nenhum arquivo de teste Java reconhecido na árvore; verificar se usa layout incomum')
        if truncated:base['new_status']='INCONCLUSIVO'
        return base
    selected=entries if args.max_files==0 else entries[:args.max_files]
    sums=dict(gwt=0,jgiven=0,comments=0,rest_only=0,other=0)
    positives=0;failed=0;examined=0;links=[];details=[]
    for e in selected:
        path=e['path']
        raw='https://raw.githubusercontent.com/'+urllib.parse.quote(name,safe='/')+'/'+sha+'/'+urllib.parse.quote(path,safe='/')
        source,st=get(raw,args.token,throttle,is_json=False,max_bytes=args.max_bytes)
        if st!='OK':
            # evita chamar blobs na API para erros HTTP temporários em massa
            failed+=1;continue
        examined+=1
        counts,examples=analyze_java(source)
        for key in sums:sums[key]+=counts[key]
        if counts['gwt']:
            positives+=1
            link='https://github.com/'+name+'/blob/'+sha+'/'+urllib.parse.quote(path,safe='/')
            if len(links)<args.evidence_limit:
                links.append(link);details.append(path+' => '+', '.join(examples))
        # Pode interromper após evidências positivas se pedido explicitamente.
        if args.stop_after_positives and positives>=args.stop_after_positives:break
    complete=(not truncated and examined+failed==len(entries) and failed==0)
    base.update(api_status='OK',files_examined=examined,files_failed=failed,
                gwt_test_methods=sums['gwt'],jgiven_test_methods=sums['jgiven'],
                comment_gwt_test_methods=sums['comments'],rest_assured_only_methods=sums['rest_only'],
                other_gwt_methods=sums['other'],files_with_positive_evidence=positives,
                evidence_urls=' | '.join(links),evidence_details=' | '.join(details),
                possible_demo_or_framework=('SIM' if DEMO.search(name) or FRAMEWORK.search(name) else 'NAO'),
                completed_scan=str(complete))
    if sums['jgiven'] or sums['comments'] or sums['other']:
        base.update(new_status='CANDIDATO_BDD_CODIGO',
                    evidence_kind=('JGiven' if sums['jgiven'] else 'Comentarios_GWT' if sums['comments'] else 'GWT_Outro'),
                    decision_reason='GWT ordenado em ao menos um método de teste; verificar semântica e projeto real')
    elif sums['rest_only']:
        base.update(new_status='REVISAR_REST_ASSURED',evidence_kind='REST_Assured_ou_HTTP',
                    decision_reason='Padrão GWT possivelmente é DSL HTTP, não BDD comportamental')
    elif not complete:
        base.update(new_status='INCONCLUSIVO',decision_reason='Arquivos não examinados, indisponíveis ou árvore truncada')
    else:
        base.update(new_status='SEM_GWT_DETECTADO',decision_reason='Nenhum método @Test com GWT ordenado nos arquivos reconhecidos')
    return base

def main():
    parser=argparse.ArgumentParser(description='Segunda passagem com refinamento Java BDD.')
    parser.add_argument('--input',default='auditoria_bdd_filtrada.csv')
    parser.add_argument('--output',default='refinamento_bdd_resultados.csv')
    parser.add_argument('--candidates',default='refinamento_bdd_candidatos.csv')
    parser.add_argument('--workers',type=int,default=3)
    parser.add_argument('--spacing',type=float,default=.4,help='Intervalo global mínimo entre requisições')
    parser.add_argument('--max-files',type=int,default=0,help='0 examina todos os arquivos de teste reconhecidos')
    parser.add_argument('--stop-after-positives',type=int,default=0,help='0 não para cedo, ou N arquivos positivos para triagem rápida')
    parser.add_argument('--max-bytes',type=int,default=3_000_000)
    parser.add_argument('--evidence-limit',type=int,default=12)
    parser.add_argument('--limit-repos',type=int,default=0)
    parser.add_argument('--include-status',default='',help='Opcional: estados anteriores, separados por vírgula')
    args=parser.parse_args()
    args.workers=min(4,max(1,args.workers));args.spacing=max(.25,args.spacing)
    args.token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if not args.token:sys.exit('Defina GH_TOKEN antes de executar; não compartilhe o token.')
    folder=Path(__file__).resolve().parent
    infile=folder/args.input
    if not infile.is_file():sys.exit(f'Não encontrei o CSV: {infile}')
    with infile.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    if not rows or 'full_name' not in rows[0]:sys.exit('CSV não possui coluna full_name')
    states={s.strip() for s in args.include_status.split(',') if s.strip()}
    if states:rows=[r for r in rows if r.get('triage_status','') in states]
    if args.limit_repos:rows=rows[:args.limit_repos]
    # Pula somente registros comprovadamente completos; falhas e varreduras incompletas são retomadas.
    outfile=folder/args.output; done=set()
    if outfile.exists():
        with outfile.open(encoding='utf-8-sig',newline='') as f:
            for row in csv.DictReader(f):
                if row.get('full_name') and row.get('api_status')=='OK' and row.get('completed_scan')=='True':
                    done.add(row['full_name'])
    rows=[r for r in rows if r['full_name'] not in done]
    print(f'[INÍCIO] {len(rows)} repositórios a inspecionar; {len(done)} completos reaproveitados. Até {args.max_files or "TODOS"} arquivos/projeto.',flush=True)
    throttle=Throttle(args.spacing)
    user,st=get('https://api.github.com/user',args.token,throttle)
    if st!='OK':sys.exit('Token inválido ou API indisponível: '+st)
    print('[AUTH] GitHub autenticado como',user.get('login','?'),flush=True)
    with outfile.open('a',encoding='utf-8' if outfile.exists() else 'utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=COLUMNS)
        if f.tell()==0:w.writeheader()
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures={pool.submit(scan,r,args,throttle):r for r in rows}
            for i,future in enumerate(concurrent.futures.as_completed(futures),1):
                try:out=future.result()
                except Exception as exc:
                    out={k:'' for k in COLUMNS};out.update(full_name=futures[future]['full_name'],api_status='ERRO_INTERNO',new_status='ERRO',decision_reason=repr(exc)[:220])
                w.writerow(out);f.flush()
                print(f'[{i}/{len(rows)}] {out["full_name"]} | {out["new_status"]} | {out["files_examined"]}/{out["java_test_files_found"]} | GWT={out["gwt_test_methods"]}',flush=True)
    # Snapshot desduplicado: se um projeto foi reavaliado, vale a última linha.
    with outfile.open(encoding='utf-8-sig',newline='') as f:
        latest={row['full_name']:row for row in csv.DictReader(f) if row.get('full_name')}
    with (folder/args.candidates).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=COLUMNS);w.writeheader()
        for row in latest.values():
            if row['new_status'] in ('CANDIDATO_BDD_CODIGO','REVISAR_REST_ASSURED','INCONCLUSIVO','ERRO'):
                w.writerow(row)
    from collections import Counter
    print('[RESUMO]',dict(Counter(r['new_status'] for r in latest.values())),flush=True)
    print('[SAÍDAS]',outfile,folder/args.candidates,flush=True)

if __name__=='__main__':main()
