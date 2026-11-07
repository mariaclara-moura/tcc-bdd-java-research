#!/usr/bin/env python3
"""Baixa arquivos ZIP dos candidatos a partir da lista já consolidada.
Não exige CSVs das etapas anteriores. Python 3.9+, sem bibliotecas externas.
"""
import argparse
import csv
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VALID_REPO = re.compile(r'^[\w.-]+/[\w.-]+$')
VALID_SHA = re.compile(r'^[a-fA-F0-9]{40}$')
FIELDS = ['repository','sha','status','path','detail']

def csv_rows(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))

def download_one(repo, sha, dest, limit_mb, timeout, allow_head):
    owner, project = repo.split('/')
    revision = sha if VALID_SHA.fullmatch(sha) else ('HEAD' if allow_head else None)
    if revision is None:
        return 'SEM_SHA', '', 'Sem commit SHA; use --allow-head se aceitar versão não fixada'
    target = dest / f'{owner}__{project}__{revision[:12]}.zip'
    if target.exists() and zipfile.is_zipfile(target):
        return 'JA_EXISTE', str(target), 'ZIP presente e válido'
    url = f'https://codeload.github.com/{urllib.parse.quote(owner)}/{urllib.parse.quote(project)}/zip/{revision}'
    req = urllib.request.Request(url, headers={'User-Agent':'TCC-BDD-academic-downloader'})
    part = target.with_suffix('.part')
    count = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            size = int(response.headers.get('Content-Length', '0') or 0)
            if size > limit_mb*1024*1024:
                return 'MUITO_GRANDE', '', f'ZIP anunciado com {size} bytes'
            with open(part,'wb') as out:
                while True:
                    chunk=response.read(1024*512)
                    if not chunk: break
                    count+=len(chunk)
                    if count>limit_mb*1024*1024:
                        raise ValueError(f'ZIP excedeu {limit_mb} MB')
                    out.write(chunk)
        if not zipfile.is_zipfile(part):
            raise ValueError('Resposta não é um ZIP válido')
        part.replace(target)
        return 'OK', str(target), f'{count} bytes'
    except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError,OSError,ValueError) as e:
        return 'ERRO', '', str(e)[:240]
    finally:
        part.unlink(missing_ok=True)

def main():
    p=argparse.ArgumentParser(description='Baixa ZIPs de repositórios BDD a partir de um CSV de candidatos')
    p.add_argument('--input', default=str(ROOT/'lista_parcial_bdd_para_revisao.csv'))
    p.add_argument('--groups', default='EXECUTAVEL_PRIORITARIO', help='Categorias separadas por vírgula ou ALL')
    p.add_argument('--dest', default=str(ROOT/'zips_bdd'))
    p.add_argument('--max-mb', type=int, default=80)
    p.add_argument('--timeout', type=int, default=90)
    p.add_argument('--delay', type=float, default=1)
    p.add_argument('--allow-head', action='store_true', help='Permite baixar HEAD quando não há SHA (menos reprodutível)')
    p.add_argument('--limit', type=int, default=0, help='0 = todos da categoria')
    a=p.parse_args()
    src=Path(a.input)
    if not src.exists(): p.error(f'Arquivo de entrada não encontrado: {src}')
    rows=csv_rows(src)
    if not rows or 'repository' not in rows[0] or 'tipo' not in rows[0]:
        p.error('CSV deve ter colunas repository e tipo')
    groups={g.strip() for g in a.groups.split(',')}
    selected=[r for r in rows if 'ALL' in groups or r.get('tipo','') in groups]
    if a.limit>0: selected=selected[:a.limit]
    dest=Path(a.dest);dest.mkdir(parents=True,exist_ok=True)
    log=dest/'downloads_status.csv'
    print(f'Arquivo: {src.name} | total: {len(rows)} | selecionados: {len(selected)}',flush=True)
    seen=set()
    if log.exists():
        for x in csv_rows(log):
            if x['status'] in ('OK','JA_EXISTE') and Path(x.get('path','')).is_file():
                seen.add((x['repository'].lower(),x['sha']))
    with open(log,'a',newline='',encoding='utf-8') as file:
        writer=csv.DictWriter(file, fieldnames=FIELDS)
        if file.tell()==0:writer.writeheader()
        for i,r in enumerate(selected,1):
            repo=r['repository'].strip();sha=r.get('last_commit_sha','').strip()
            if not VALID_REPO.fullmatch(repo):
                status,path,detail='NOME_INVALIDO','','Repository fora do padrão owner/name'
            elif (repo.lower(),sha) in seen:
                print(f'[{i}/{len(selected)}] {repo}: já baixado',flush=True)
                continue
            else:
                status,path,detail=download_one(repo,sha,dest,a.max_mb,a.timeout,a.allow_head)
            writer.writerow(dict(repository=repo,sha=sha,status=status,path=path,detail=detail))
            file.flush()
            print(f'[{i}/{len(selected)}] {repo}: {status} {detail}',flush=True)
            if status in ('OK','JA_EXISTE'): seen.add((repo.lower(),sha))
            if a.delay>0:time.sleep(a.delay)
    print(f'Concluído. Relatório: {log}',flush=True)

if __name__=='__main__':main()
