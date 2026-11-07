# Pesquisa de testes BDD em Java — TCC

Repositório de trabalho da coleta, triagem e revisão de repositórios Java que contêm testes no estilo Given–When–Then (GWT).

## Organização

- `scripts/`: versões de busca, auditoria e refinamento extraídas da pasta original `tcc/`; `scripts/legacy/` guarda cópias históricas distintas.
- `data/raw/`: resultados originais de buscas no GitHub.
- `data/intermediate/`: consolidações, filtros e filas.
- `data/audits/`: resultados de inspeção dos testes Java.
- `data/exclusions/`: exclusões documentadas pelas coletas.
- `data/candidates/`: listas automáticas e inspeções intermediárias.
- `data/manual/decisoes_manuais.csv`: **decisões revisadas pela pesquisadora**, preservando as etiquetas originais e justificativas.
- `data/manual/fonte_original_decisoes.tsv`: cópia fiel da tabela enviada.
- `docs/manifesto_arquivos.csv`: proveniência e SHA-256 dos arquivos preservados.
- `docs/arquivos_duplicados_na_origem.csv`: arquivos repetidos no ZIP original e cópia preservada.

## Decisões manuais

Foram identificados **32 repositórios anotados**. Totais por decisão:

- `APROVADO`: 2
- `APROVADO + VERIFICAR`: 1
- `POSSIVEL`: 11
- `REPROVADO`: 12
- `VALIDAR`: 6

O campo `APROVADO + VERIFICAR` permanece separado de `APROVADO`. `VALIDAR` e `POSSIVEL` **não** são aprovações. A tabela de origem reuniu dois formatos de linhas: as colunas adicionais foram identificadas pelo bloco de origem, sem forçar equivalência entre classificações.

## Critério de inclusão em evolução

Busca-se GWT executável em métodos Java (JGiven ou convenções/DSLs), sem exigir alta qualidade prévia: o instrumento de 12 questões mede a qualidade. Comentários isolados de GWT são analisados separadamente. Excluir demonstrações da técnica/framework BDD; não excluir automaticamente exemplos de outras tecnologias apenas por serem demonstrações.

## Segurança e publicação

O pacote **não inclui** ambientes virtuais, bancos SQLite de retomada, logs, ZIPs baixados de terceiros, `.DS_Store` e caches. Eles permanecem no arquivo `tcc.zip` original do usuário. Antes de publicar, revise URLs e dados de pesquisa; não salve tokens em arquivos versionados. Scripts históricos não foram auditados funcionalmente e não devem ser apresentados como um pipeline único validado.

### Envio ao GitHub

```bash
git init
git branch -M main
git add .
git commit -m "Organiza coletas, auditorias e decisões manuais de BDD Java"
gh repo create tcc-bdd-java-research --private --source=. --remote=origin --push
```
