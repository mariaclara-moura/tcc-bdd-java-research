#!/usr/bin/env python3
"""Triagem de repositórios NOVOS descobertos via GitHub Code Search.

Python 3.9+, somente biblioteca padrão. Critérios iniciais: >=1 estrela e
último commit da branch padrão nos últimos N dias. Prioriza arquivos Java
localizados pela busca de código. Sinais GWT são candidatos, não aprovações.

Exemplo:
  python3 -u auditar_novos_bdd_java.py \
    --input nova_busca_bdd_repositorios_novos_3406.csv \
    --files complementary_bdd_java_files.csv \
    --workers 2 --max-files 30

Para inspecionar todos os arquivos reconhecidos, use --max-files 0.
Retoma por full_name a partir dos arquivos de saída. Erros são reprocessados.
"""
import argparse
import csv
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from concurrent.futures import ThreadPoolExecutor, as_completed

FIELDS = [
 'full_name','repository_url','priority','stars','fork','archived','pushed_at',
 'default_branch','last_commit_at','last_commit_sha','api_status',
 'triage_status','reason','tree_truncated','java_test_files_total',
 'files_examined','file_errors','gwt_methods','jgiven_methods','comment_methods',
 'other_methods','rest_assured_methods','cucumber_step_files',
 'evidence_file_urls','evidence_methods','found_from_search_files',
 'complete_scan','review_required','checked_at_utc'
]
TEST_FOLDER = re.compile(r'/(?:test|tests|tst|src/it|src/integrationtest|integration-tests|integrationtests|acceptance|functionaltest|functional-tests)/',re.I)
TEST_SUFFIX = re.compile(r'(?:Test|Tests|IT|Spec|Specification|Scenario|ScenarioTest)\.java$',re.I)
SKIP = ('/target/','/build/','/generated/','/node_modules/','/vendor/','/.gradle/','/dist/')
ANNOT = re.compile(r'@(?:Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate|org\.junit\.[\w.]+\.Test|org\.testng\.annotations\.Test)\b')
METHOD = re.compile(r'\b(?:public|private|protected|static|final|synchronized|\s)*\b(?:void|[\w<>\[\],?]+)\s+(\w+)\s*\([^;{}]*\)\s*(?:throws\s+[\w., ]+)?\s*\{',re.S)
G = re.compile(r'\bgiven\s*\(\s*\)|\bgiven\s*\.|//\s*given\b|/\*+\s*given\b|\bgiven_',re.I)
W = re.compile(r'\bwhen\s*\(\s*\)|\bwhen\s*\.|//\s*when\b|/\*+\s*when\b|\bwhen_',re.I)
T = re.compile(r'\bthen\s*\(\s*\)|\bthen\s*\.|//\s*then\b|/\*+\s*then\b|\bthen_',re.I)
STAGES = re.compile(r'@ScenarioStage\b|com\.tngtech\.jgiven|SimpleScenarioTest|ScenarioTest\s*<',re.I)
REST = re.compile(r'io\.restassured|RestAssured|RequestSpecification',re.I)
CUCUMBER = re.compile(r'io\.cucumber\.|cucumber\.api\.',re.I)

def now(): return datetime.now(timezone.utc).isoformat()
def valid_date(v):
 try: return datetime.fromisoformat((v or '').replace('Z','+00:00')).astimezone(timezone.utc)
 except (ValueError,TypeError): return None

def is_test(path):
 p='/'+path.replace('\\','/').lstrip('/')
 if not p.lower().endswith('.java') or any(x in p.lower() for x in SKIP): return False
 return bool(TEST_FOLDER.search(p) or TEST_SUFFIX.search(p.rsplit('/',1)[-1]))

def read_csv(path):
 with open(path,encoding='utf-8-sig',newline='') as f: return list(csv.DictReader(f))

def load_matches(path):
 ans={}
 if not path or not Path(path).exists(): return ans
 for row in read_csv(path):
  name=row.get('full_name','').lower()
  p=row.get('file_path','')
  if name and p and is_test(p): ans.setdefault(name,set()).add(p)
 return ans

class Pacer:
 def __init__(self,space): self.space=space;self.lock=threading.Lock();self.next_time=0
 def wait(self):
  with self.lock:
   delta=self.next_time-time.monotonic()
   if delta>0: time.sleep(delta)
   self.next_time=time.monotonic()+self.space

def request(url,token,pacer,api=True,max_bytes=2_500_000):
 for n in range(6):
  pacer.wait()
  headers={'User-Agent':'java-bdd-tcc-audit','Accept':'application/vnd.github+json' if api else 'text/plain'}
  if api and token: headers['Authorization']='Bearer '+token
  try:
   with urlopen(Request(url,headers=headers), timeout=35) as response:
    raw=response.read(max_bytes+1)
    if len(raw)>max_bytes: return None,'TOO_LARGE'
    return (json.loads(raw) if api else raw.decode('utf-8','replace')),'OK'
  except HTTPError as exc:
   if exc.code in (401,404,410,451): return None,'HTTP_'+str(exc.code)
   if exc.code in (403,429):
    remaining=exc.headers.get('X-RateLimit-Remaining','')
    reset=exc.headers.get('X-RateLimit-Reset','')
    retry=exc.headers.get('Retry-After','')
    if remaining=='0' and reset and reset.isdigit(): delay=max(5,int(reset)-int(time.time())+5)
    elif retry.isdigit(): delay=max(15,min(300,int(retry)))
    else: delay=min(300,40*(n+1))
    print(f'[LIMITE] {exc.code}, aguardando {delay}s',flush=True)
    time.sleep(delay);continue
   if exc.code>=500: time.sleep(min(40,2**n));continue
   return None,'HTTP_'+str(exc.code)
  except (URLError,TimeoutError,ValueError,OSError):
   if n==5:return None,'NETWORK_ERROR'
   time.sleep(min(30,2**n))
 return None,'RETRY_EXHAUSTED'

def methods(src):
 seen=set()
 for m in ANNOT.finditer(src):
  h=METHOD.search(src[m.end():m.end()+1400])
  if not h:continue
  start=m.end()+h.end()-1
  if start in seen:continue
  seen.add(start)
  depth=0
  for j in range(start,min(len(src),start+180000)):
   if src[j]=='{':depth+=1
   elif src[j]=='}':
    depth-=1
    if depth==0:
     yield h.group(1),src[start:j+1]
     break

def analyze(src):
 counts={'gwt':0,'jgiven':0,'comment':0,'other':0,'rest':0,'cucumber':0}
 samples=[]
 jgiven=bool(STAGES.search(src)); rest=bool(REST.search(src));cucumber=bool(CUCUMBER.search(src))
 if cucumber: counts['cucumber']=1
 for method,body in methods(src):
  g=G.search(body);w=W.search(body);t=T.search(body)
  if not(g and w and t and g.start()<w.start()<t.start()):continue
  counts['gwt']+=1
  comments=all(re.search(r'//\s*'+x+r'\b',body,re.I) for x in ('given','when','then'))
  fluent=all(re.search(r'\b'+x+r'\s*\(\s*\)\s*\.',body,re.I) for x in ('given','when','then'))
  if jgiven and fluent: kind='JGiven';counts['jgiven']+=1
  elif comments: kind='GWT_COMENTARIOS';counts['comment']+=1
  elif rest: kind='REST_ASSURED_POSSIVEL';counts['rest']+=1
  else: kind='GWT_OUTRO';counts['other']+=1
  if len(samples)<6:samples.append(method+':'+kind)
 return counts,samples

def priority(path,matched):
 p=path.lower();score=1000 if path in matched else 0
 for needle,points in [('jgiven',60),('scenario',35),('bdd',35),('behavior',25),('behaviour',25),('acceptance',20),('test',8)]:
  if needle in p:score+=points
 if 'stepdefinition' in p:score-=60
 return (-score,path)

def scan(row,args,pacer,matches):
 name=row['full_name'].strip();out={x:'' for x in FIELDS}
 out.update(full_name=name,repository_url=row.get('repository_url') or 'https://github.com/'+name,
            priority=row.get('prioridade_busca',''),api_status='OK',review_required='SIM',checked_at_utc=now())
 def api(suffix):return request('https://api.github.com/'+suffix,args.token,pacer)
 def finish(status,reason,api_status=None):
  out['triage_status']=status;out['reason']=reason
  if api_status:out['api_status']=api_status
  return out
 meta,status=api('repos/'+quote(name,safe='/'))
 if status!='OK':return finish('ERRO','Metadados indisponíveis',status)
 out['stars']=meta.get('stargazers_count',0);out['fork']=meta.get('fork',False)
 out['archived']=meta.get('archived',False);out['pushed_at']=meta.get('pushed_at') or ''
 branch=meta.get('default_branch') or 'main';out['default_branch']=branch
 if meta.get('fork') and args.exclude_forks:return finish('EXCLUIDO_FORK','Fork excluído pela opção explícita')
 if meta.get('archived'):return finish('EXCLUIDO_ARQUIVADO','Repositório arquivado')
 if int(out['stars'])<args.min_stars:return finish('EXCLUIDO_ESTRELAS','Abaixo do mínimo de estrelas')
 cutoff=datetime.now(timezone.utc)-timedelta(days=args.days)
 pushed=valid_date(meta.get('pushed_at'))
 if pushed and pushed<cutoff:return finish('EXCLUIDO_PUSH_ANTIGO','Último push anterior à janela (sem baixar testes)')
 commit,status=api('repos/'+quote(name,safe='/')+'/commits/'+quote(branch,safe=''))
 if status!='OK':return finish('ERRO','Não foi possível verificar último commit',status)
 sha=commit.get('sha','');out['last_commit_sha']=sha
 info=commit.get('commit',{});date=(info.get('committer') or {}).get('date') or (info.get('author') or {}).get('date') or ''
 out['last_commit_at']=date
 if not valid_date(date):return finish('INCONCLUSIVO','Data do último commit não disponível')
 if valid_date(date)<cutoff:return finish('EXCLUIDO_COMMIT_ANTIGO','Último commit na branch padrão anterior à janela')
 if not sha:return finish('ERRO','SHA da branch padrão indisponível')
 tree,status=api('repos/'+quote(name,safe='/')+'/git/trees/'+quote(sha,safe='')+'?recursive=1')
 if status!='OK':return finish('ERRO','Não foi possível consultar a árvore de arquivos',status)
 out['tree_truncated']=str(bool(tree.get('truncated')))
 entries=[it for it in tree.get('tree',[]) if it.get('type')=='blob' and is_test(it.get('path',''))]
 matched=matches.get(name.lower(),set())
 entries.sort(key=lambda it:priority(it['path'],matched))
 out['java_test_files_total']=len(entries)
 out['found_from_search_files']=len(set(it['path'] for it in entries)&matched)
 if not entries:
  return finish('INCONCLUSIVO' if tree.get('truncated') else 'SEM_TESTE_JAVA_RECONHECIDO','Não foram encontrados caminhos Java de teste na árvore')
 if args.max_files>0:entries=entries[:args.max_files]
 total={'gwt':0,'jgiven':0,'comment':0,'other':0,'rest':0,'cucumber':0}
 evidence_urls=[];evidence_methods=[]; errors=0
 for it in entries:
  path=it['path']
  url='https://raw.githubusercontent.com/'+quote(name,safe='/')+'/'+quote(sha,safe='')+'/'+quote(path,safe='/')
  src,st=request(url,args.token,pacer,api=False)
  if st!='OK':errors+=1;continue
  counts,samples=analyze(src)
  for key,val in counts.items():total[key]+=val
  if counts['gwt'] and len(evidence_urls)<15:
   evidence_urls.append('https://github.com/'+name+'/blob/'+sha+'/'+quote(path,safe='/'))
   evidence_methods.extend(path+':'+s for s in samples[:6])
 out.update(files_examined=len(entries),file_errors=errors,gwt_methods=total['gwt'],
  jgiven_methods=total['jgiven'],comment_methods=total['comment'],other_methods=total['other'],
  rest_assured_methods=total['rest'],cucumber_step_files=total['cucumber'],
  evidence_file_urls=' | '.join(evidence_urls),evidence_methods=' | '.join(evidence_methods[:30]))
 complete=(not tree.get('truncated') and errors==0 and len(entries)==int(out['java_test_files_total']))
 out['complete_scan']=str(complete)
 if total['jgiven'] or total['comment'] or total['other']:
  return finish('CANDIDATO_BDD_CODIGO','Método de teste com indícios ordenados de Given/When/Then; validar semântica e autenticidade')
 if total['rest']:return finish('REVISAR_REST_ASSURED','Indícios GWT ligados a DSL HTTP; verificar manualmente')
 if not complete:return finish('INCONCLUSIVO','Arquivos não examinados, falhas de download ou árvore truncada')
 return finish('SEM_GWT_DETECTADO','Nenhum padrão GWT reconhecido pelos critérios heurísticos')

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--input',default='nova_busca_bdd_repositorios_novos_3406.csv')
 p.add_argument('--files',default='complementary_bdd_java_files.csv')
 p.add_argument('--output',default='new_bdd_repositories_audit.csv')
 p.add_argument('--excluded',default='new_bdd_audit_exclusions.csv')
 p.add_argument('--min-stars',type=int,default=1)
 p.add_argument('--days',type=int,default=365)
 p.add_argument('--max-files',type=int,default=30,help='0=todos; padrão=30 priorizados')
 p.add_argument('--workers',type=int,default=2)
 p.add_argument('--spacing',type=float,default=0.55,help='Espaçamento global entre requisições HTTP (segundos)')
 p.add_argument('--exclude-forks',action='store_true',help='Opcional; não exclui forks por padrão')
 p.add_argument('--retry-inconclusive',action='store_true',help='Reprocessa inconclusivos em execução posterior')
 p.add_argument('--dry-run',action='store_true')
 args=p.parse_args()
 args.token=os.getenv('GH_TOKEN') or os.getenv('GITHUB_TOKEN')
 if not args.token:raise SystemExit('Configure GH_TOKEN ou GITHUB_TOKEN')
 rows=read_csv(args.input)
 rows=[r for r in rows if '/' in r.get('full_name','')]
 seen=set();rows=[r for r in rows if not (r['full_name'].lower() in seen or seen.add(r['full_name'].lower()))]
 matches=load_matches(args.files)
 stable={'EXCLUIDO_ESTRELAS','EXCLUIDO_PUSH_ANTIGO','EXCLUIDO_COMMIT_ANTIGO','EXCLUIDO_ARQUIVADO','EXCLUIDO_FORK','SEM_GWT_DETECTADO','CANDIDATO_BDD_CODIGO','REVISAR_REST_ASSURED','SEM_TESTE_JAVA_RECONHECIDO'}
 done={}
 for file in (args.output,args.excluded):
  if Path(file).exists():
   for r in read_csv(file):
    if r.get('triage_status') in stable or (r.get('triage_status')=='INCONCLUSIVO' and not args.retry_inconclusive):done[r['full_name'].lower()]=1
 pending=[r for r in rows if r['full_name'].lower() not in done]
 print(f'[INÍCIO] {len(rows)} distintos, {len(done)} já registrados, {len(pending)} pendentes; arquivos de busca para {len(matches)} repositórios',flush=True)
 if args.dry_run:return
 pacer=Pacer(args.spacing)
 for path in (args.output,args.excluded):
  if not Path(path).exists() or Path(path).stat().st_size==0:
   with open(path,'w',encoding='utf-8-sig',newline='') as f:csv.DictWriter(f,fieldnames=FIELDS).writeheader()
 count=0
 with ThreadPoolExecutor(max_workers=max(1,min(args.workers,4))) as ex:
  futures={ex.submit(scan,r,args,pacer,matches):r['full_name'] for r in pending}
  for fut in as_completed(futures):
   name=futures[fut]
   try:result=fut.result()
   except Exception as exc:
    result={k:'' for k in FIELDS};result.update(full_name=name,api_status='PYTHON_ERROR',triage_status='ERRO',reason=repr(exc),checked_at_utc=now())
   target=args.excluded if result['triage_status'].startswith('EXCLUIDO') else args.output
   with open(target,'a',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=FIELDS);w.writerow(result);f.flush()
   count+=1
   print(f'[{count}/{len(pending)}] {name}: {result["triage_status"]}; arquivos={result["files_examined"] or 0}; GWT={result["gwt_methods"] or 0}',flush=True)
 print('[FIM] Auditoria concluída. CSVs:',args.output,args.excluded,flush=True)

if __name__=='__main__':main()
