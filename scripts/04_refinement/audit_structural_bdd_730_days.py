#!/usr/bin/env python3
"""Triagem de testes BDD executaveis Java (JGiven/DSL ou convencoes de metodos).

Entrada recomendada: java_repositories_minimum_1_star.csv (2519 repositorios).
Usar a entrada dos 312 NAO permite recuperar projetos excluidos por idade.

Biblioteca padrao Python >=3.9. Resultados incrementais e retomaveis.
Esta e uma TRIAGEM SINTATICA, nao uma aprovacao de elegibilidade cientifica.
"""
import argparse
import concurrent.futures
import csv
import datetime as dt
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

FIELDS = ['full_name','url','stars','pushed_at','last_commit_date','last_commit_sha',
          'api_status','decision','reason','files_found','files_scanned','files_failed',
          'tree_truncated','jgiven_methods','executable_convention_methods',
          'ambiguous_methods','rest_assured_methods','comment_only_methods',
          'evidence_urls','evidence_methods','complete_scan','scan_time_utc']
TERMINAL = {'EXCLUIDO_ESTRELAS','EXCLUIDO_ARQUIVADO','EXCLUIDO_PUSH_ANTIGO',
            'EXCLUIDO_COMMIT_ANTIGO','CANDIDATO_ESTRUTURAL','REVISAR_AMBIGUIDADE',
            'APENAS_COMENTARIOS','SEM_BDD_EXECUTAVEL','SEM_TESTES_RECONHECIDOS',
            'INCONCLUSIVO'}
TEST_ANNOT = re.compile(r'@(?:Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate|org\.junit\.(?:jupiter\.api\.)?(?:Test|ParameterizedTest)|org\.testng\.annotations\.Test)\b')
METHOD_HEAD = re.compile(r'(?:(?:public|private|protected|static|final|synchronized)\s+)*[\w<>\[\],.?]+\s+(\w+)\s*\([^;{}]*\)\s*(?:throws\s+[\w., ]+)?\s*\{',re.S)
GIVEN = re.compile(r'\b(?:given\s*\(\s*\)|given\s*\.|given[A-Z_]\w*\s*\(|given_\w+\s*\()',re.I)
WHEN = re.compile(r'\b(?:when\s*\(\s*\)|when\s*\.|when[A-Z_]\w*\s*\(|when_\w+\s*\()',re.I)
THEN = re.compile(r'\b(?:then\s*\(\s*\)|then\s*\.|then[A-Z_]\w*\s*\(|then_\w+\s*\()',re.I)
# Explicit JGiven stage entry points or variables; no ordinary bare given/when/then from HTTP clients.
JGiven = re.compile(r'com\.tngtech\.jgiven|SimpleScenarioTest|\bScenarioTest\s*<|@ScenarioStage\b|\bGivenStage\b|\bWhenStage\b|\bThenStage\b')
REST = re.compile(r'io\.restassured|RestAssured|RequestSpecification|ResponseSpecification',re.I)
MOCKITO = re.compile(r'org\.mockito|BDDMockito|Mockito\.',re.I)
COMMENT_GWT = re.compile(r'(?m)^\s*(?://|/\*+|\*)\s*(given|when|then)\b',re.I)
EXCLUDE_PARTS = ['/target/','/build/','/out/','/node_modules/','/generated/','/vendor/','.gradle/','/dist/']

class Pace:
    def __init__(self,spacing): self.spacing=spacing;self.lock=threading.Lock();self.last=0.0
    def wait(self):
        with self.lock:
            delay=self.last+self.spacing-time.monotonic()
            if delay>0:time.sleep(delay)
            self.last=time.monotonic()

def fetch(url,pace,token,json_mode=True,retries=4,max_size=4000000):
    for i in range(retries):
        pace.wait()
        headers={'User-Agent':'tcc-bdd-structural-audit','Accept':'application/vnd.github+json' if json_mode else 'text/plain'}
        if token and url.startswith('https://api.github.com/'):headers['Authorization']='Bearer '+token
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=35) as r:
                b=r.read(max_size+1)
                if len(b)>max_size:return None,'TOO_LARGE'
                return (json.loads(b) if json_mode else b.decode('utf-8','replace')),'OK'
        except urllib.error.HTTPError as e:
            if e.code==401:return None,'HTTP_401'
            if e.code in (403,429):
                remain=e.headers.get('X-RateLimit-Remaining','?')
                reset=e.headers.get('X-RateLimit-Reset')
                if remain=='0' and reset:
                    wait=max(5,int(reset)-int(time.time())+5)
                else:
                    wait=min(120,30*(i+1))
                print(f'[RATE] {e.code}; remaining={remain}; pausa {wait}s',flush=True)
                time.sleep(wait);continue
            if e.code>=500:time.sleep(min(20,2**i));continue
            return None,'HTTP_'+str(e.code)
        except (OSError,TimeoutError,ValueError):
            time.sleep(min(20,2**i))
    return None,'ERRO_REDE_OU_LIMITE'

def api(name,suffix,pace,token):
    return fetch('https://api.github.com/repos/'+urllib.parse.quote(name,safe='/')+suffix,pace,token)

def parse_date(value):
    try:return dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    except (ValueError,AttributeError):return None

def test_file(path):
    p='/'+path.lower().lstrip('/')
    if not p.endswith('.java') or any(x in p for x in EXCLUDE_PARTS):return False
    basename=p.rsplit('/',1)[-1]
    folder=bool(re.search(r'/(?:test|tests|tst|src/it|src/integrationtest|integration-tests|integrationtests|acceptance-tests|functional-tests|src/testfixtures)/',p))
    named=bool(re.search(r'(?:test|tests|testcase|it|spec|scenario|scenariotest)\.java$',basename))
    return folder or named

def priority(path):
    x=path.lower()
    return sum(weight for word,weight in [('jgiven',30),('bdd',25),('stage',15),('scenario',15),('acceptance',8),('test',3)] if word in x)

def mask_noncode(src):
    """Replace comments + strings with spaces preserving indexes & linebreaks."""
    chars=list(src); n=len(src); i=0
    while i<n:
        if src.startswith('//',i):
            j=src.find('\n',i);j=n if j<0 else j
            for k in range(i,j):chars[k]=' '
            i=j;continue
        if src.startswith('/*',i):
            j=src.find('*/',i+2);j=n if j<0 else j+2
            for k in range(i,j):
                if chars[k]!='\n':chars[k]=' '
            i=j;continue
        if src.startswith('"""',i):
            j=src.find('"""',i+3);j=n if j<0 else j+3
            for k in range(i,j):
                if chars[k]!='\n':chars[k]=' '
            i=j;continue
        if src[i] in ('"',"'"):
            quote=src[i];j=i+1
            while j<n:
                if src[j]=='\\':j+=2;continue
                if src[j]==quote:j+=1;break
                j+=1
            for k in range(i,min(j,n)):
                if chars[k]!='\n':chars[k]=' '
            i=j;continue
        i+=1
    return ''.join(chars)

def methods(src):
    clean=mask_noncode(src)
    for a in TEST_ANNOT.finditer(clean):
        header=clean[a.end():a.end()+1800]
        m=METHOD_HEAD.search(header)
        if not m:continue
        start=a.end()+m.end()-1
        if start>=len(src):continue
        depth=0
        for k in range(start,min(len(src),start+300000)):
            if clean[k]=='{':depth+=1
            elif clean[k]=='}':
                depth-=1
                if depth==0:
                    yield m.group(1),src[start:k+1],clean[start:k+1]
                    break

def classify(src):
    has_jgiven=bool(JGiven.search(src));has_rest=bool(REST.search(src));has_mockito=bool(MOCKITO.search(src))
    counts={'jgiven':0,'convention':0,'ambiguous':0,'rest':0,'comments':0}; examples=[]
    for name,original,body in methods(src):
        a=GIVEN.search(body);b=WHEN.search(body);c=THEN.search(body)
        comment_names=[m.group(1).lower() for m in COMMENT_GWT.finditer(original)]
        is_comment=len(comment_names)>=3 and all(k in comment_names for k in ('given','when','then'))
        if not (a and b and c and a.start()<b.start()<c.start()):
            if is_comment:counts['comments']+=1
            continue
        given=body[a.start():a.end()];when=body[b.start():b.end()];then=body[c.start():c.end()]
        # Plain given()/when()/then() with RestAssured is often HTTP DSL and not BDD steps.
        plain=all(re.fullmatch(r'(?:given|when|then)\s*\(\s*\)',x) for x in (given,when,then))
        if has_jgiven and not (has_rest and not re.search(r'com\.tngtech\.jgiven|SimpleScenarioTest|ScenarioTest\s*<',src)):
            kind='jgiven'
        elif has_rest and plain:
            kind='rest'
        elif has_mockito and plain:
            kind='ambiguous'
        elif all(re.search(r'(?:[A-Z_]|\.)',x) for x in (given,when,then)):
            kind='convention'
        else:
            kind='ambiguous'
        counts[kind]+=1
        if len(examples)<8:examples.append(name+':'+kind)
    return counts,examples

def new_row(name,old):
    row={k:'' for k in FIELDS}
    row.update(full_name=name,url='https://github.com/'+name,
        stars=old.get('stars_atual') or old.get('stars') or '',
        scan_time_utc=dt.datetime.now(dt.timezone.utc).isoformat())
    return row

def scan(repo,args,pace,old):
    name=repo['full_name'].strip();row=new_row(name,old)
    try:
        stars=int(float(repo.get('stars') or 0))
    except ValueError:stars=0
    if stars<args.min_stars:
        row.update(decision='EXCLUIDO_ESTRELAS',reason='CSV: menos de 1 estrela',api_status='OK');return row
    # Reuse recent metadata from prior audit only; otherwise refresh it.
    known_commit=old.get('last_commit_date','');known_sha=old.get('last_commit_sha','')
    reusable=(bool(known_commit and known_sha) and not args.refresh_metadata)
    if reusable:
        row['last_commit_date']=known_commit;row['last_commit_sha']=known_sha
        row['pushed_at']=old.get('pushed_at','')
        row['stars']=old.get('stars_atual') or row['stars']
    else:
        meta,status=api(name,'',pace,args.token)
        if status!='OK' or not isinstance(meta,dict):
            row.update(api_status=status,decision='ERRO',reason='Erro ao consultar repositório');return row
        row['stars']=meta.get('stargazers_count',row['stars'])
        row['pushed_at']=meta.get('pushed_at','')
        if meta.get('archived'):
            row.update(api_status='OK',decision='EXCLUIDO_ARQUIVADO',reason='Repositório arquivado');return row
        if int(row['stars'] or 0)<args.min_stars:
            row.update(api_status='OK',decision='EXCLUIDO_ESTRELAS',reason='Estrelas atuais abaixo do mínimo');return row
        push=parse_date(row['pushed_at'])
        if push and push<args.cutoff:
            row.update(api_status='OK',decision='EXCLUIDO_PUSH_ANTIGO',reason='Sem push em 730 dias');return row
        branch=meta.get('default_branch') or 'main'
        commits,status=api(name,'/commits?sha='+urllib.parse.quote(branch,safe='')+'&per_page=1',pace,args.token)
        if status!='OK' or not isinstance(commits,list) or not commits:
            row.update(api_status=status,decision='ERRO',reason='Erro ao consultar último commit');return row
        c=commits[0];row['last_commit_sha']=c['sha'];row['last_commit_date']=(c.get('commit',{}).get('committer',{}) or {}).get('date','')
    when=parse_date(row['last_commit_date'])
    if not when:
        row.update(api_status='OK',decision='ERRO',reason='Data do commit ausente');return row
    if when<args.cutoff:
        row.update(api_status='OK',decision='EXCLUIDO_COMMIT_ANTIGO',reason='Último commit da branch principal anterior ao corte');return row
    sha=row['last_commit_sha']
    tree,status=api(name,'/git/trees/'+urllib.parse.quote(sha,safe='')+'?recursive=1',pace,args.token)
    if status!='OK' or not isinstance(tree,dict):
        row.update(api_status=status,decision='ERRO',reason='Falha ao obter árvore Git');return row
    truncated=bool(tree.get('truncated'))
    row['tree_truncated']=str(truncated)
    paths=[obj['path'] for obj in tree.get('tree',[]) if obj.get('type')=='blob' and test_file(obj.get('path',''))]
    paths.sort(key=lambda p:(-priority(p),p));row['files_found']=len(paths)
    if not paths:
        row.update(api_status='OK',decision='INCONCLUSIVO' if truncated else 'SEM_TESTES_RECONHECIDOS',reason='Nenhum caminho de teste Java reconhecido',complete_scan=str(not truncated))
        return row
    selected=paths if args.max_files==0 else paths[:args.max_files]
    counts={'jgiven':0,'convention':0,'ambiguous':0,'rest':0,'comments':0};examined=failed=0;urls=[];details=[]
    for path in selected:
        raw='https://raw.githubusercontent.com/'+urllib.parse.quote(name,safe='/')+'/'+sha+'/'+urllib.parse.quote(path,safe='/')
        content,status=fetch(raw,pace,args.token,json_mode=False,max_size=args.max_bytes)
        if status!='OK':failed+=1;continue
        examined+=1
        sub,examples=classify(content)
        for k in counts:counts[k]+=sub[k]
        if sub['jgiven'] or sub['convention'] or sub['ambiguous'] or sub['rest']:
            if len(urls)<args.evidence_limit:
                urls.append('https://github.com/'+name+'/blob/'+sha+'/'+urllib.parse.quote(path,safe='/'))
                details.append(path+' => '+', '.join(examples))
    complete=not truncated and failed==0 and examined==len(paths)
    row.update(api_status='OK',files_scanned=examined,files_failed=failed,
        jgiven_methods=counts['jgiven'],executable_convention_methods=counts['convention'],
        ambiguous_methods=counts['ambiguous'],rest_assured_methods=counts['rest'],
        comment_only_methods=counts['comments'],evidence_urls=' | '.join(urls),
        evidence_methods=' | '.join(details),complete_scan=str(complete))
    if counts['jgiven'] or counts['convention']:
        row.update(decision='CANDIDATO_ESTRUTURAL',reason='Estrutura executável no método de teste; validar semântica e projeto')
    elif counts['ambiguous'] or counts['rest']:
        row.update(decision='REVISAR_AMBIGUIDADE',reason='Chamadas GWT possivelmente de HTTP/Mockito ou convenção não comprovada')
    elif not complete:
        row.update(decision='INCONCLUSIVO',reason='Não foi possível verificar todos os arquivos')
    elif counts['comments']:
        row.update(decision='APENAS_COMENTARIOS',reason='GWT encontrado em comentários, sem evidência executável')
    else:
        row.update(decision='SEM_BDD_EXECUTAVEL',reason='Nenhuma sequência executável identificada em método de teste')
    return row

def load_csv(path):
    with open(path,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def write_outputs(data,path,positive):
    temp=path.with_suffix(path.suffix+'.tmp')
    with temp.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader()
        for row in data.values():writer.writerow({k:row.get(k,'') for k in FIELDS})
    temp.replace(path)
    selected=[r for r in data.values() if r.get('decision') in ('CANDIDATO_ESTRUTURAL','REVISAR_AMBIGUIDADE','INCONCLUSIVO','ERRO')]
    temp=positive.with_suffix(positive.suffix+'.tmp')
    with temp.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader()
        writer.writerows({k:r.get(k,'') for k in FIELDS} for r in selected)
    temp.replace(positive)

def main():
    p=argparse.ArgumentParser(description='Auditoria estrutural Java BDD, janela 730 dias')
    p.add_argument('--input',default='java_repositories_minimum_1_star.csv')
    p.add_argument('--previous',default='auditoria_bdd_filtrada_completa.csv')
    p.add_argument('--output',default='structural_bdd_730d_results.csv')
    p.add_argument('--candidates',default='structural_bdd_730d_candidates.csv')
    p.add_argument('--days',type=int,default=730)
    p.add_argument('--min-stars',type=int,default=1)
    p.add_argument('--workers',type=int,default=2)
    p.add_argument('--spacing',type=float,default=0.55)
    p.add_argument('--max-files',type=int,default=0)
    p.add_argument('--max-bytes',type=int,default=3_000_000)
    p.add_argument('--evidence-limit',type=int,default=12)
    p.add_argument('--refresh-metadata',action='store_true',help='Reconsultar metadados mesmo de projetos anteriores')
    p.add_argument('--limit-repos',type=int,default=0,help='Teste rápido; 0=todos')
    args=p.parse_args()
    args.workers=min(3,max(1,args.workers));args.spacing=max(.3,args.spacing)
    args.token=os.getenv('GH_TOKEN') or os.getenv('GITHUB_TOKEN')
    if not args.token: p.error('Configure GH_TOKEN ou GITHUB_TOKEN')
    args.cutoff=dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=args.days)
    folder=Path(__file__).resolve().parent
    source=folder/args.input;previous=folder/args.previous
    if not source.exists():p.error('CSV não encontrado: '+str(source))
    repos=load_csv(source)
    if args.limit_repos:repos=repos[:args.limit_repos]
    old={r['full_name']:r for r in load_csv(previous)} if previous.exists() else {}
    output=folder/args.output; candidates=folder/args.candidates
    completed={r['full_name']:r for r in load_csv(output)} if output.exists() else {}
    tasks=[r for r in repos if r.get('full_name') and (r['full_name'] not in completed or completed[r['full_name']].get('decision')=='ERRO')]
    pace=Pace(args.spacing)
    print(f'[INICIO] {len(repos)} repositórios; pendentes={len(tasks)}; janela={args.days} dias; trabalhadores={args.workers}',flush=True)
    print('[CRITERIO] somente JGiven/DSL e convenção executável; comentários são categoria separada',flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures={executor.submit(scan,r,args,pace,old.get(r['full_name'],{})):r['full_name'] for r in tasks}
        for i,future in enumerate(concurrent.futures.as_completed(futures),1):
            name=futures[future]
            try:result=future.result()
            except Exception as err:
                result=new_row(name,{});result.update(decision='ERRO',api_status='EXCEPTION',reason=repr(err)[:250])
            completed[name]=result
            write_outputs(completed,output,candidates)
            print(f'[{i}/{len(tasks)}] {name}: {result["decision"]}; JGiven={result["jgiven_methods"] or 0} convenção={result["executable_convention_methods"] or 0} arquivos={result["files_scanned"] or 0}',flush=True)
    print('[FIM] Salvos:',output,candidates,flush=True)

if __name__=='__main__': main()
