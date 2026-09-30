from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

import yaml
from cache_ratelimit import CachedProvider, RateLimiter, RealClock, ThrottledProvider
from llm_client import CompletionRequest, CompletionResult, LlmClient, ReplayProvider, Span
from llm_client.providers.opencode_cli import OpenCodeCLI
from parser_io import ParsedDocument, iter_documents, parse
from schema_validate import SchemaRegistry
from secure_base import sanitize_for_prompt
from test_kit import EvalCase, EvalDataset, run

from .models import SiteDigest

DEFAULT_MODEL = "opencode/big-pickle"
SCHEMA_ID = "site-digest-v1"
PROMPT_ID = "site-digest-generator"
ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = ROOT / "prompts"
DEFAULT_PROMPT = PROMPT_DIR / "site-digest-generator.md"
DEFAULT_DATASET = ROOT / "evals" / "site-digest.jsonl"
DEFAULT_CORPUS = ROOT.parent / "llm-dev-core" / "docs" / "decisions"
DEFAULT_OUT = ROOT / "site"
TTL_DAILY = 86400.0


def load_prompt(path: Path) -> tuple[str, str, str, Path]:
    text = path.read_text()
    if not text.startswith("---"):
        raise SystemExit(f"{path}: falta frontmatter")
    _, frontmatter, body = text.split("---", 2)
    data = yaml.safe_load(frontmatter)
    eval_path = ROOT / data["eval"] if not Path(data["eval"]).is_absolute() else Path(data["eval"])
    return data["id"], data["version"], body.strip(), eval_path


def render_docs_gen(prompt_id: str, prompt_version: str, variables: dict) -> list[dict]:
    """Templado para `site-digest-generator`; el índice (contenido del repo) entra
    sanitizado con `secure-base` antes de cruzar al proveedor."""
    if prompt_id == PROMPT_ID:
        _, _, body, _ = load_prompt(DEFAULT_PROMPT)
        system_part = body.split("## Sistema\n", 1)[1].split("## Usuario\n", 1)[0].strip()
        user_template = body.split("## Usuario\n", 1)[1].strip()
        user_raw = user_template.format(**variables)
        return [
            {"role": "system", "content": system_part},
            {"role": "user", "content": sanitize_for_prompt(user_raw).text},
        ]
    payload = {"prompt_id": prompt_id, "prompt_version": prompt_version, "variables": variables}
    return [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def build_emitter(span_file: Path | None):
    def emit(span: Span, _result) -> None:
        if span_file is not None:
            with span_file.open("a") as handle:
                handle.write(span.as_jsonl() + "\n")
        else:
            sys.stderr.write(span.as_jsonl() + "\n")

    return emit


def build_client(inner_provider, *, clock=None, span_file=None) -> dict:
    clock = RealClock() if clock is None else clock
    cached = CachedProvider(inner_provider, ttl_seconds=TTL_DAILY, clock=clock)
    limiter = RateLimiter(rpm=120, burst=20, clock=clock)
    provider = ThrottledProvider(cached, limiter)
    registry = SchemaRegistry()
    registry.register(SCHEMA_ID, SiteDigest)
    client = LlmClient(
        provider,
        consumer_repo="docs-gen",
        model_aliases={"fast": DEFAULT_MODEL},
        renderer=render_docs_gen,
        validator=registry.make_validator(SCHEMA_ID),
        emitter=build_emitter(span_file),
    )
    return {"client": client, "cached": cached, "limiter": limiter}


def read_corpus(corpus: Path) -> tuple[ParsedDocument, ...]:
    """Los ADRs normalizados con parser-io; un archivo que no parsea se saltea y se avisa."""
    docs: list[ParsedDocument] = []
    for path in iter_documents(corpus):
        try:
            docs.append(parse(path))
        except Exception as exc:  # noqa: BLE001
            print(f"salteado {exc}")
    return tuple(docs)


def slug_of(doc: ParsedDocument) -> str:
    return Path(doc.source).stem


def index_panel(docs: tuple[ParsedDocument, ...]) -> str:
    """Índice para el prompt: por ADR, número de secciones/tablas y presupuesto de tokens."""
    lines = []
    for doc in docs:
        lines.append(
            f"- {slug_of(doc)}: {doc.title} · {len(doc.sections)} secciones · "
            f"{len(doc.tables)} tablas · ~{doc.approx_tokens} tokens"
        )
    return "\n".join(lines) or "(sin ADRs)"


def digest_request(docs: tuple[ParsedDocument, ...], *, corpus: Path) -> CompletionRequest:
    return CompletionRequest(
        prompt_id=PROMPT_ID,
        prompt_version="0.1.0",
        variables={
            "date": date.today().isoformat(),
            "docs": str(len(docs)),
            "est_tokens": str(sum(d.approx_tokens for d in docs)),
            "indice": index_panel(docs),
        },
        model_alias="fast",
        response_schema=SCHEMA_ID,
        tags=["docs-gen", "week-12"],
    )


def render_index(docs: tuple[ParsedDocument, ...], digest: SiteDigest, *, date_str: str) -> str:
    """index.md: tabla con estadísticas reales del ParsedDocument + narrativa validada."""
    rows = []
    for doc in docs:
        slug = slug_of(doc)
        blurb = next((a.blurb for a in digest.adrs if a.slug == slug), "—")
        rows.append(
            f"| [{slug}](adr/{slug}.md) | {len(doc.sections)} | {len(doc.tables)} | ~{doc.approx_tokens} | {blurb} |"
        )
    table = "\n".join(rows) or "| _sin ADRs_ | - | - | - | - |"
    return (
        f"# llm-dev-core — decisiones de arquitectura\n\n"
        f"> {digest.tagline}\n\n"
        f"## ADRs\n\n"
        f"(Cuerpo y números: `parser-io`. Narrativa: LLM validado contra `site-digest-v1`, {date_str}.)\n\n"
        f"| ADR | Secciones | Tablas | Tokens | Resumen |\n"
        f"|---|---|---|---|---|\n{table}\n"
    )


def render_adr(doc: ParsedDocument, digest: SiteDigest) -> str:
    """Página por ADR: blurb (LLM) + cuerpo íntegro del ParsedDocument (sin LLM)."""
    blurb = next((a.blurb for a in digest.adrs if a.slug == slug_of(doc)), None)
    sections = "\n\n".join(f"## {s.title}\n\n{s.body.strip()}" for s in doc.sections)
    body = f"\n\n{sections}\n" if sections else "\n\n_(sin secciones)_\n"
    header = "> " + blurb if blurb else ""
    return f"# {doc.title}\n\n{header}{body}"


def build(
    corpus: Path,
    out_dir: Path,
    *,
    client: LlmClient,
) -> tuple[SiteDigest, dict[str, str]]:
    docs = read_corpus(corpus)
    result = client.complete(digest_request(docs, corpus=corpus))
    if not result.validation.ok:
        raise SystemExit("; ".join(result.validation.errors))
    files = {f"{slug_of(doc)}.md": render_adr(doc, result.parsed) for doc in docs}
    files["index.md"] = render_index(docs, result.parsed, date_str=date.today().isoformat())
    return result.parsed, files


def judge_for(client: LlmClient) -> Callable[[EvalCase], CompletionResult]:
    def judge(case: EvalCase) -> CompletionResult:
        return client.complete(
            CompletionRequest(
                prompt_id=case.prompt_id,
                prompt_version=case.prompt_version,
                variables=case.input,
                model_alias="fast",
                response_schema=SCHEMA_ID,
                tags=["docs-gen", "week-12"],
            )
        )

    return judge


def run_replay_check() -> int:
    dataset = EvalDataset.from_jsonl(DEFAULT_DATASET)
    provider = ReplayProvider(ROOT / "cassettes", record=False)
    client = build_client(provider)["client"]
    report = run(dataset, judge_for(client), mode="full", threshold=1.0)
    print(f"replay: pass={report.passed}/{report.total} threshold_ok={report.threshold_ok}")
    return 0 if report.threshold_ok else 1


def run_record() -> int:
    dataset = EvalDataset.from_jsonl(DEFAULT_DATASET)
    provider = ReplayProvider(ROOT / "cassettes", record=True, inner=OpenCodeCLI(DEFAULT_MODEL))
    client = build_client(provider)["client"]
    report = run(dataset, judge_for(client), mode="full", threshold=1.0)
    print(f"grabadas {len(dataset.cases)} respuestas; pass={report.passed}/{report.total}")
    return 0 if report.threshold_ok else 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="docs-gen: sitio de documentación desde ADRs (semana 12).")
    parser.add_argument("--in", dest="corpus", type=Path, default=DEFAULT_CORPUS, help="corpus de ADRs")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="directorio del sitio generado")
    parser.add_argument(
        "command",
        nargs="?",
        default="build",
        choices=["build", "replay-check", "record"],
        help="build genera el sitio; replay-check/record usan la cinta (D4).",
    )
    args = parser.parse_args(argv)

    if args.command in ("replay-check", "record"):
        raise SystemExit(run_record() if args.command == "record" else run_replay_check())

    client = build_client(OpenCodeCLI(DEFAULT_MODEL))["client"]
    digest, files = build(args.corpus, args.out, client=client)
    for name, content in files.items():
        target = args.out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    print(f"docs-gen: {len(files) - 1} ADRs → {args.out} (tagline: {digest.tagline})")


if __name__ == "__main__":
    main()