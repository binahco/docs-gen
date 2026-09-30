# docs-gen

Genera el **sitio de documentación** del core (semana 12) desde los ADRs que viven en
`llm-dev-core/docs/decisions`: `parser-io` normaliza cada ADR y el cuerpo sale **sin LLM**
del `ParsedDocument`; el LLM redacta solo la narrativa (`site-digest-v1`) — tagline y un
blurb por ADR. Es el lado *write* del documento: `content-ray` (sem 10) lee y resume,
`docs-gen` lee y escribe el sitio.

## Demo

```bash
uv sync
uv run docs-gen build --in ../llm-dev-core/docs/decisions --out ./site
# docs-gen: 10 ADRs → site (tagline: …)      — narrativa LLM validada
tree site
# site/
# ├── index.md
# ├── 0001-llm-client-contract.md
# ├── … (una página por ADR)
# └── 0009-parser-io-contract.md
```

El único texto LLM por página es el blurb (validado contra `site-digest-v1`): el cuerpo,
las secciones, las tablas y los conteos salen del `ParsedDocument` (determinista). Para
ver solo la cinta sin grabar (D4):

```bash
uv run docs-gen replay-check   # valida el dataset congelado contra el cassette 3/3
```

## Cómo funciona

```
docs-gen build --in CORPUS --out SITE
  ├─ read_corpus(CORPUS)        # parser-io: cada ADR → ParsedDocument (secciones/tablas/líneas/tokens)
  ├─ index_panel(docs)          # índice para el LLM: estadísticas REALES por ADR
  ├─ client.complete(site-digest-generator)  # tagline + blurbs, validado y sanitizado
  └─ render                     # index.md = tabla de estadísticas + narrativa
                                # <slug>.md = blurb (LLM) + cuerpo íntegro (ParsedDocument)
```

- El cuerpo de cada ADR **nunca** se reescribe ni pasa por un modelo: se vierte tal cual.
- El índice que ve el LLM lleva los conteos calculados por `parser-io`, no los que el
  modelo diga.
- `build` falla (exit ≠ 0) si la narrativa no valida contra `site-digest-v1`.

## Recicla de

| Módulo | Uso |
|---|---|
| `parser-io` | la capa de lectura: `parse`/`iter_documents` → `ParsedDocument` (ADR-9) |
| `llm-client` | la llamada de la narrativa (retry + reparación + span de 20 campos) |
| `schema-validate` | la narrativa valida contra `site-digest-v1` o el build falla |
| `test-kit` | el prompt nace evaluado: dataset congelado + replay determinista (D4) |
| `secure-base` | el índice (contenido del repo) se redacta antes de cruzar al proveedor |
| `cache-ratelimit` | caché TTL diario + token bucket por delante del proveedor |
| `ci-pack` | lints del audit y workflow `eval-smoke.yml` |

## Limitaciones

- El sitio es markdown plano deliberadamente: un sitio HTML con estilos es de
  `docs-gen` v2 o de un convertidor fuera del core.
- Las páginas no atraviesan el cuerpo para narrar (solo el índice): narrar secciones
  reales es un caso de `content-ray` con chunks (`vector-core`, sem. 16).

## Roadmap

- [x] Sitio `markdown` desde ADRs: index + página por ADR (sem. 12)
- [x] Cuerpo desde `ParsedDocument` sin LLM; narrativa validada (`site-digest-v1`)
- [ ] Sitio HTML navegable (v2) sobre la misma base
- [ ] Narrativa desde el cuerpo (chunks) cuando llegue `vector-core`