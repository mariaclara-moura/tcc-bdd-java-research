#!/usr/bin/env python3
"""Busca complementar e reproduzível de indícios BDD em código Java no GitHub.

Usa a API REST LEGADA /search/code (não a sintaxe nova do site GitHub).
Resultados são indícios, não confirmação da estrutura Given-When-Then.

Dependência: python3 -m pip install requests
Autenticação: export GH_TOKEN='...' (ou GITHUB_TOKEN)

Executar: python3 -u busca_complementar_bdd_java.py --existing github_repositories_merged.csv --existing github_code_search_repositories_merged.csv
Pré-visualização: python3 busca_complementar_bdd_java.py --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    sys.exit("Dependência ausente: execute python3 -m pip install requests")

API_URL = "https://api.github.com/search/code"
# Apenas consultas complementares; NÃO repetem jgiven, given(), when() ou then() isolados.
# Consultas usam a sintaxe LEGADA aceita pela API REST, sem regex, OR, content:, stars: ou pushed:.
# 'filename:' e 'path:' são qualificadores aceitos na API legada.
QUERY_GROUPS = [
    ("JGiven_superclasse", "SimpleScenarioTest language:Java"),
    ("JGiven_superclasse", "ScenarioTest language:Java"),
    ("JGiven_estagios", "ScenarioStage language:Java"),
    ("JGiven_estagios", "GivenStage language:Java"),
    ("JGiven_estagios", "WhenStage language:Java"),
    ("JGiven_estagios", "ThenStage language:Java"),
    ("GWT_comentarios", '"// Given" language:Java'),
    ("GWT_comentarios", '"// When" language:Java'),
    ("GWT_comentarios", '"// Then" language:Java'),
    ("GWT_nomeados", "given_ language:Java"),
    ("GWT_nomeados", "when_ language:Java"),
    ("GWT_nomeados", "then_ language:Java"),
    ("GWT_nomeados", "givenThat language:Java"),
    ("GWT_nomeados", "whenThe language:Java"),
    ("GWT_nomeados", "thenThe language:Java"),
    ("DSL_BDD_ampliada", "net.serenitybdd language:Java"),
    ("DSL_BDD_ampliada", "org.jbehave language:Java"),
    ("DSL_BDD_ampliada", "BDDMockito language:Java"),
]
# A API REST legado permite buscas sem path; filtramos os caminhos após a recuperação.
# Termos "// Given" etc. e BDDMockito são amplos: possíveis falsos positivos.

# Estratégia de expansão: se alguma busca satura 1000, particionar mais tarde por path/filename.
# Muitas buscas de comentários podem produzir falsos positivos: guardar para revisão, não aceitar automaticamente.

TEST_SEGMENTS = {"test", "tests", "tst", "it", "integration-test", "integration-tests", "integrationtest", "integrationtests", "acceptance", "acceptance-test", "acceptance-tests"}
TEST_NAME = re.compile(r"(?:Test|Tests|IT|ITCase|Spec|Specification)\.java$", re.IGNORECASE)


def test_path(path: str) -> bool:
    if not path or not path.lower().endswith(".java"):
        return False
    segments = path.lower().split("/")
    if any(p in {"target", "build", "generated", "vendor", "node_modules", "testdata"} for p in segments):
        return False
    return bool(TEST_SEGMENTS.intersection(segments) or TEST_NAME.search(path))


def db_connect(path: Path):
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS pages (
      query TEXT NOT NULL, page INTEGER NOT NULL, total_count INTEGER,
      incomplete_results INTEGER, received INTEGER, stored INTEGER,
      fetched_at TEXT NOT NULL,
      PRIMARY KEY (query, page))""")
    db.execute("""CREATE TABLE IF NOT EXISTS hits (
      full_name TEXT NOT NULL, path TEXT NOT NULL, query TEXT NOT NULL,
      category TEXT NOT NULL, file_url TEXT, repo_url TEXT,
      sha TEXT, repo_description TEXT, stars INTEGER, forks INTEGER,
      PRIMARY KEY(full_name, path, query))""")
    db.commit()
    return db


def existing_names(paths):
    out = set()
    for p in paths:
        file = Path(p)
        if not file.exists():
            print(f"[AVISO] Arquivo de comparação não encontrado: {file}", file=sys.stderr)
            continue
        with file.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                continue
            key = next((k for k in ("full_name", "repository", "repo_full_name") if k in reader.fieldnames), None)
            if not key:
                print(f"[AVISO] {file} não possui coluna full_name/repository: não usado para deduplicação externa", file=sys.stderr)
                continue
            for row in reader:
                s = (row.get(key) or "").strip().lower()
                if s:
                    out.add(s)
    return out


class RequestThrottle:
    """Garante o intervalo mínimo entre TODAS as requisições, inclusive entre consultas."""
    def __init__(self, seconds):
        self.seconds = seconds
        self.last_request = None

    def wait(self):
        if self.last_request is not None:
            remaining = self.seconds - (time.monotonic() - self.last_request)
            if remaining > 0:
                time.sleep(remaining)
        self.last_request = time.monotonic()


def request_page(session, query, page, throttle):
    # Respeita limite de 10 req/min e falhas secundárias. Execução sequencial intencional.
    for attempt in range(8):
        throttle.wait()
        try:
            resp = session.get(API_URL, params={"q": query, "per_page": 100, "page": page}, timeout=(10, 50))
        except requests.RequestException as exc:
            wait = min(120, 5 * 2 ** attempt)
            print(f"[REDE] {exc}; nova tentativa em {wait}s", flush=True)
            time.sleep(wait)
            continue
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 401:
            raise RuntimeError("401: token inválido. Confira GH_TOKEN/GITHUB_TOKEN.")
        if resp.status_code == 422:
            raise RuntimeError(f"422: consulta não aceita ({query}): {resp.text[:350]}")
        if resp.status_code in (403, 429):
            remaining = resp.headers.get("X-RateLimit-Remaining")
            reset = resp.headers.get("X-RateLimit-Reset")
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                wait = max(8, int(retry_after) + 2)
            elif remaining == "0" and reset and reset.isdigit():
                wait = max(8, int(reset) - int(time.time()) + 3)
            else:
                wait = min(300, 30 * (attempt + 1))
            print(f"[LIMITE HTTP {resp.status_code}] aguardar {wait}s; {resp.text[:160]}", flush=True)
            time.sleep(wait)
            continue
        if resp.status_code in (500, 502, 503, 504):
            wait = min(120, 10 * 2 ** attempt)
            print(f"[HTTP {resp.status_code}] nova tentativa em {wait}s", flush=True)
            time.sleep(wait)
            continue
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:400]}")
    raise RuntimeError(f"Falha persistente ao executar consulta: {query}, página {page}")


def insert_page(db, query, category, page, data):
    items = data.get("items", [])
    n = 0
    with db:
        for item in items:
            repo = item.get("repository") or {}
            name = (repo.get("full_name") or "").strip()
            path = (item.get("path") or "").strip()
            if not name or not test_path(path):
                continue
            db.execute("""INSERT OR IGNORE INTO hits
                (full_name,path,query,category,file_url,repo_url,sha,repo_description,stars,forks)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", (
                  name, path, query, category, item.get("html_url") or "",
                  repo.get("html_url") or f"https://github.com/{name}", item.get("sha") or "",
                  repo.get("description") or "", repo.get("stargazers_count"), repo.get("forks_count")
                ))
            n += 1
        db.execute("""INSERT OR REPLACE INTO pages
            (query,page,total_count,incomplete_results,received,stored,fetched_at)
            VALUES(?,?,?,?,?,?,?)""", (
              query,page,data.get("total_count",0),int(bool(data.get("incomplete_results"))),
              len(items),n,datetime.now(timezone.utc).isoformat()
            ))
    return n


def export_results(db, prefix, known):
    prefix = Path(prefix)
    files_path = Path(str(prefix) + "_arquivos.csv")
    repos_path = Path(str(prefix) + "_repositorios.csv")
    queries_path = Path(str(prefix) + "_consultas.csv")
    matched = db.execute("""SELECT full_name,path,query,category,file_url,repo_url,sha,repo_description,stars,forks
        FROM hits ORDER BY full_name,path,query""").fetchall()
    filedict = {}
    repodict = {}
    for full, path, query, cat, url, repourl, sha, desc, stars, forks in matched:
        key = (full, path)
        f = filedict.setdefault(key, {"full_name":full,"file_path":path,"file_url":url,
                                      "repository_url":repourl,"sha":sha,"categories":set(),"matched_queries":set()})
        f["categories"].add(cat)
        f["matched_queries"].add(query)
        r = repodict.setdefault(full, {"full_name":full,"repository_url":repourl,
            "repository_description":desc,"stars_from_search":stars,"forks_from_search":forks,
            "files":set(),"categories":set(),"matched_queries":set()})
        r["files"].add(path)
        r["categories"].add(cat)
        r["matched_queries"].add(query)
    with files_path.open("w",newline="",encoding="utf-8-sig") as fp:
        fields = ["full_name","file_path","file_url","repository_url","sha","categories","matched_queries"]
        w=csv.DictWriter(fp,fieldnames=fields); w.writeheader()
        for f in filedict.values():
            row=dict(f); row["categories"]="; ".join(sorted(f["categories"])); row["matched_queries"]="; ".join(sorted(f["matched_queries"])); w.writerow(row)
    with repos_path.open("w",newline="",encoding="utf-8-sig") as fp:
        fields = ["full_name","repository_url","repository_description","stars_from_search","forks_from_search",
                  "matched_file_count","categories","matched_queries","already_in_previous_search","matched_test_files"]
        w=csv.DictWriter(fp,fieldnames=fields); w.writeheader()
        for r in repodict.values():
            row={k:v for k,v in r.items() if k != "files"}
            row.update({"matched_file_count":len(r["files"]),
                        "categories":"; ".join(sorted(r["categories"])),
                        "matched_queries":"; ".join(sorted(r["matched_queries"])),
                        "already_in_previous_search": "SIM" if r["full_name"].lower() in known else "NAO",
                        "matched_test_files":"; ".join(sorted(r["files"]))})
            w.writerow(row)
    rows=db.execute("""SELECT query, MAX(total_count), MAX(incomplete_results), SUM(received), SUM(stored),COUNT(*)
        FROM pages GROUP BY query""").fetchall()
    with queries_path.open("w",newline="",encoding="utf-8-sig") as fp:
        fields=["category","query","github_total_count","incomplete_results","received_items","test_file_hits","pages_completed","saturated_1000"]
        w=csv.DictWriter(fp,fieldnames=fields);w.writeheader()
        cat_map={q:c for c,q in QUERY_GROUPS}
        for q,total,inc,received,stored,pages in rows:
            w.writerow({"category":cat_map.get(q,""),"query":q,"github_total_count":total,
                        "incomplete_results":inc,"received_items":received,"test_file_hits":stored,
                        "pages_completed":pages,"saturated_1000":"SIM" if total>=1000 else "NAO"})
    new=sum(r.lower() not in known for r in repodict)
    print(f"\n[EXPORTADO] {len(filedict)} arquivos; {len(repodict)} repositórios; {new} novos em relação aos CSVs anteriores.",flush=True)
    print(f"[ARQUIVOS] {files_path} | {repos_path} | {queries_path}",flush=True)


def main():
    p=argparse.ArgumentParser(description="Busca complementar de BDD Java no GitHub, com retomada e evidência por arquivo.")
    p.add_argument("--existing",action="append",default=[],help="CSV anterior de repositórios para sinalizar sobreposição; repetir para vários arquivos")
    p.add_argument("--database",default="busca_complementar_bdd_java.sqlite3",help="Banco local para retomar páginas sem repetição")
    p.add_argument("--output-prefix",default="busca_complementar_bdd_java",help="Prefixo dos três CSVs de saída")
    p.add_argument("--pages",type=int,default=10,help="Máximo de páginas por consulta (até 10)")
    p.add_argument("--spacing",type=float,default=7.0,help="Segundos entre requisições (mínimo de 6.5)")
    p.add_argument("--dry-run",action="store_true",help="Lista consultas sem chamar API")
    p.add_argument("--export-only",action="store_true",help="Exporta CSVs do banco sem novas requisições")
    args=p.parse_args()
    if not 1<=args.pages<=10:p.error("--pages deve estar entre 1 e 10")
    if args.spacing<6.5:p.error("--spacing não pode ser menor que 6.5 segundos")
    if args.dry_run:
        for n,(cat,q) in enumerate(QUERY_GROUPS,1):print(f"{n:02d}. [{cat}] {q}")
        print(f"\n{len(QUERY_GROUPS)} consultas. Busca legada REST; arquivos Java e caminhos de testes são validados após a busca.")
        return
    known=existing_names(args.existing)
    db=db_connect(Path(args.database))
    if args.export_only:
        export_results(db,args.output_prefix,known);return
    token=os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:sys.exit("Defina GH_TOKEN ou GITHUB_TOKEN antes de executar.")
    throttle=RequestThrottle(args.spacing)
    session=requests.Session()
    session.headers.update({"Accept":"application/vnd.github+json","Authorization":f"Bearer {token}",
                            "X-GitHub-Api-Version":"2022-11-28","User-Agent":"academic-bdd-java-code-search"})
    print(f"[INÍCIO] {len(QUERY_GROUPS)} consultas | {len(known)} repositórios conhecidos | {datetime.now(timezone.utc).isoformat()}",flush=True)
    try:
        for index,(cat,query) in enumerate(QUERY_GROUPS,1):
            first=db.execute("SELECT total_count FROM pages WHERE query=? AND page=1",(query,)).fetchone()
            total=first[0] if first else None
            print(f"\n[{index}/{len(QUERY_GROUPS)}] {cat}: {query}",flush=True)
            for page in range(1,args.pages+1):
                if total is not None and (page-1)*100>=min(total,1000):break
                saved=db.execute("SELECT total_count, received FROM pages WHERE query=? AND page=?",(query,page)).fetchone()
                if saved:
                    total=saved[0]
                    print(f"  Página {page}: já gravada ({saved[1]} resultados); pulando",flush=True)
                    if saved[1]<100:break
                    continue
                data=request_page(session,query,page,throttle)
                total=data.get("total_count",0)
                n=insert_page(db,query,cat,page,data)
                print(f"  Página {page}: {len(data.get('items',[]))} resultados, {n} arquivos Java de teste; total informado={total}",flush=True)
                if page==1 and total>=1000:
                    print("  [ATENÇÃO] Busca saturada em 1.000; esta consulta NÃO é exaustiva. Consulte o CSV de consultas.",flush=True)
                if not data.get("items") or len(data["items"])<100:break
            export_results(db,args.output_prefix,known)
    finally:
        export_results(db,args.output_prefix,known)
        db.close()

if __name__=="__main__":
    main()
