#!/usr/bin/env python3
"""Audita cada repositório de full_screening_7203.csv via GitHub REST API.
Uso: GH_TOKEN=ghp_... python inspect_github_java_tests.py --input full_screening_7203.csv
Continuar: executar o mesmo comando; resultados salvos incrementalmente em initial_code_inspection_results.csv.
NÃO atribui automaticamente o status de elegível: constata apenas indícios estáticos.
"""
import os,csv,re,json,time,base64,argparse,urllib.request,urllib.error,datetime,collections
from pathlib import Path
P=Path(__file__).resolve().parent
parser=argparse.ArgumentParser();parser.add_argument('--input',default='full_screening_7203.csv');parser.add_argument('--output',default='initial_code_inspection_results.csv');parser.add_argument('--max-repos',type=int,default=0,help='0=todos');parser.add_argument('--max-files',type=int,default=7,help='Quantidade de arquivos Java por projeto; 0=todos (mais lento)');parser.add_argument('--sleep',type=float,default=.15);args=parser.parse_args()
token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
if not token:raise SystemExit('Defina GH_TOKEN com um token GitHub de leitura (permissão Contents: read para repositórios públicos).')
def api(path):
 url='https://api.github.com/'+path.lstrip('/')
 req=urllib.request.Request(url,headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','User-Agent':'tcc-bdd-audit','X-GitHub-Api-Version':'2022-11-28'})
 for attempt in range(4):
  try:
   with urllib.request.urlopen(req,timeout=40) as r:return json.load(r),dict(r.headers)
  except urllib.error.HTTPError as e:
   if e.code in (403,429):
    reset=e.headers.get('X-RateLimit-Reset');remaining=e.headers.get('X-RateLimit-Remaining')
    if remaining=='0' or e.code==429:
     seconds=max(30,int(reset)-int(time.time())+3) if reset else 90
     print('Limite de API atingido; aguarde',seconds,'segundos (Ctrl+C interrompe com checkpoint salvo).',flush=True);time.sleep(min(seconds,3700));continue
   if e.code in (404,409,451):return {'_error':f'HTTP_{e.code}'},{}
   if attempt==3:return {'_error':f'HTTP_{e.code}'},{}
   time.sleep(2**attempt)
  except Exception as e:
   if attempt==3:return {'_error':str(e)[:120]},{}
   time.sleep(2**attempt)
 return {'_error':'tentativas_esgotadas'},{}
def is_test(p):
 s=p.lower();return s.endswith('.java') and (('/src/test/' in '/'+s) or ('/test/' in '/'+s) or s.startswith('test/')) and not ('/target/' in s or '/generated/' in s or '/build/' in s)
def candidate_rank(p):
 s=p.lower();return 25*('bdd' in s or 'scenario' in s or 'jgiven' in s)+8*(s.endswith('test.java') or s.endswith('tests.java'))+5*('integration' in s)+3*('acceptance' in s)
def analyze(code):
 # textual indicators; DO NOT confuse RestAssured's HTTP DSL with business-DSL qualification
 g=bool(re.search(r'\b(?:given\s*\(|given\s*:|//\s*given\b|/\*\s*given\b)',code,re.I))
 w=bool(re.search(r'\b(?:when\s*\(|when\s*:|//\s*when\b|/\*\s*when\b)',code,re.I))
 t=bool(re.search(r'\b(?:then\s*\(|then\s*:|//\s*then\b|/\*\s*then\b)',code,re.I))
 j=bool(re.search(r'\b(?:SimpleScenarioTest|ScenarioTest|Stage<|com\.tngtech\.jgiven)\b',code))
 rest=bool(re.search(r'\b(?:io\.restassured|RestAssured|given\s*\(\s*\)\s*\.\s*(?:contentType|header|body|spec|auth))',code))
 # Distinct method blocks can be checked manually. Presence of all tokens in file is only preliminary.
 test=bool(re.search(r'@Test\b|@ParameterizedTest\b|@RepeatedTest\b|extends\s+\w*ScenarioTest',code))
 return {'gwt_tokens':g and w and t,'jgiven_indicator':j,'rest_assured_indicator':rest,'test_annotation':test}
rows=list(csv.DictReader(open(P/args.input,encoding='utf-8-sig',newline='')))
out=P/args.output;existing={}
if out.exists():
 for r in csv.DictReader(open(out,encoding='utf-8-sig',newline='')):existing[r['full_name']]=r
fields=['full_name','url','scan_time_utc','api_status','java_test_files_total','examined_files','files_with_gwt_tokens','files_with_jgiven_indicator','files_with_rest_assured_indicator','example_file_urls','source_observation','last_commit_date','last_commit_sha','commits_in_last_12_months','final_manual_review']
# order prioritizes plausible production projects, but eventually reads all entries
rows.sort(key=lambda r:(r['metadata_review']!='CANDIDATO_NAO_VERIFICADO',-float(r['priority_score'])))
new=0
with out.open('a',newline='',encoding='utf-8-sig') as f:
 writer=csv.DictWriter(f,fieldnames=fields)
 if f.tell()==0:writer.writeheader()
 for row in rows:
  name=row['full_name'];
  if name in existing:continue
  if args.max_repos and new>=args.max_repos:break
  result={k:'' for k in fields};result.update(full_name=name,url=row['url'],scan_time_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),final_manual_review='NECESSARIA')
  meta,_=api('repos/'+name)
  if '_error' in meta:
   result['api_status']=meta['_error'];writer.writerow(result);f.flush();new+=1;continue
  branch=meta.get('default_branch','main');tree,_=api('repos/'+name+'/git/trees/'+urllib.parse.quote(branch,safe='')+'?recursive=1')
  if '_error' in tree or tree.get('truncated'):
   result['api_status']='TREE_TRUNCATED_OR_ERROR:'+str(tree.get('_error',''));writer.writerow(result);f.flush();new+=1;continue
  paths=[x for x in tree.get('tree',[]) if x.get('type')=='blob' and is_test(x.get('path',''))]
  paths.sort(key=lambda x:(-candidate_rank(x['path']),x['path']))
  result['java_test_files_total']=len(paths)
  nfiles=len(paths) if args.max_files==0 else min(len(paths),args.max_files)
  result['examined_files']=nfiles
  matched=[];jgiven=[];rest=[]
  for item in paths[:nfiles]:
   blob,_=api('repos/'+name+'/git/blobs/'+item['sha'])
   if '_error' in blob or blob.get('encoding')!='base64':continue
   src=base64.b64decode(blob['content']).decode('utf-8',errors='replace')
   flags=analyze(src)
   url='https://github.com/'+name+'/blob/'+branch+'/'+item['path']
   if flags['gwt_tokens'] and flags['test_annotation']:matched.append(url)
   if flags['jgiven_indicator']:jgiven.append(url)
   if flags['rest_assured_indicator']:rest.append(url)
  result['files_with_gwt_tokens']=len(matched)
  result['files_with_jgiven_indicator']=len(jgiven)
  result['files_with_rest_assured_indicator']=len(rest)
  result['example_file_urls']=' | '.join((matched+jgiven)[:5])
  result['source_observation']='SINTAXE_GWT_CANDIDATA' if matched else ('JGiven_IMPORT_OU_CLASSE' if jgiven else 'NAO_DETECTADA_NOS_ARQUIVOS_EXAMINADOS')
  last,_=api('repos/'+name+'/commits?per_page=1')
  if isinstance(last,list) and last:
   result['last_commit_sha']=last[0]['sha'];result['last_commit_date']=last[0]['commit']['committer']['date']
  # No count calculated: Github API commit page count is not a reliable total.
  result['commits_in_last_12_months']='NAO_AVALIADO'
  result['api_status']='OK';writer.writerow(result);f.flush();new+=1
  if new%25==0:print('Repositorios inspecionados nesta execucao:',new,'| salvos:',out,flush=True)
  time.sleep(args.sleep)
print('Concluido nesta execucao:',new,'repositorios; arquivo:',out)
