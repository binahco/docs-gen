from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from docs_gen.main import (
    DEFAULT_DATASET,
    build,
    build_client,
    index_panel,
    judge_for,
    read_corpus,
    render_adr,
    render_docs_gen,
    render_index,
)
from llm_client.provider import ProviderRequest, ProviderResponse
from test_kit import EvalDataset, run as run_evalset

STUB_TEXT = (
    '{"tagline": "decisiones con aceptación por semana", "adrs": '
    '[{"slug": "a", "title": "A", "blurb": "blurb de a"}, '
    '{"slug": "b", "title": "B", "blurb": "blurb de b"}]}'
)


class StubProvider:
    name = "stub"

    def __init__(self, text: str | None = None) -> None:
        self.text = text or STUB_TEXT
        self.calls = 0

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        self.last_request = request
        return ProviderResponse(text=self.text, model=request.model)

    def stream(self, request: ProviderRequest) -> list[str]:
        self.calls += 1
        return [self.text]


class FakeClock:
    def __init__(self, tick: float = 0.0) -> None:
        self._tick = tick

    def monotonic(self) -> float:
        return self._tick

    def sleep(self, seconds: float) -> None:
        self._tick += seconds

    def now(self) -> datetime:
        return datetime(2026, 10, 15, 8, 0, 0)


def _corpus(tmp_path: Path) -> Path:
    (tmp_path / "a.md").write_text("# A\n\n## Decisión\n\naceptamos X.\n\n| K | V |\n|---|---|\n| a | 1 |\n")
    (tmp_path / "b.md").write_text("# B\n\n## Reglas\n\nregla Y.\n")
    return tmp_path


def test_read_corpus_normaliza_adrs(tmp_path: Path) -> None:
    docs = read_corpus(_corpus(tmp_path))
    assert [d.format for d in docs] == ["markdown", "markdown"]
    assert docs[0].sections[0].title == "Decisión"
    assert len(docs[0].tables) == 1


def test_index_panel_lleva_estadisticas_reales(tmp_path: Path) -> None:
    docs = read_corpus(_corpus(tmp_path))
    panel = index_panel(docs)
    assert "a: A · 1 secciones · 1 tablas" in panel
    assert "b: B · 1 secciones · 0 tablas" in panel


def test_render_index_usa_numeros_del_parse_y_blurb_del_digest(tmp_path: Path) -> None:
    docs = read_corpus(_corpus(tmp_path))
    from docs_gen.models import AdrBlurb, SiteDigest

    digest = SiteDigest(tagline="tag", adrs=[AdrBlurb(slug="a", title="A", blurb="blurb de a")])
    index = render_index(docs, digest, date_str="2026-10-15")
    assert "| [a](a.md) | 1 | 1 |" in index
    assert "blurb de a" in index
    assert "blurb de b" not in index


def test_render_adr_cuerpo_es_del_parse_no_del_llm(tmp_path: Path) -> None:
    docs = read_corpus(_corpus(tmp_path))
    from docs_gen.models import AdrBlurb, SiteDigest

    digest = SiteDigest(tagline="tag", adrs=[AdrBlurb(slug="a", title="A", blurb="blurb de a")])
    page = render_adr(docs[0], digest)
    assert "aceptamos X." in page  # cuerpo real del ParsedDocument
    assert "blurb de a" in page
    assert "regla Y." not in page  # el cuerpo del otro ADR no cuela


def test_build_genera_sitio_determinista_con_stub(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    stub = StubProvider()
    client = build_client(stub, clock=FakeClock())["client"]
    _, files = build(corpus, tmp_path / "out", client=client)
    assert set(files) == {"index.md", "a.md", "b.md"}
    assert "tagline" not in files["a.md"] and "blurb de a" in files["a.md"]
    assert stub.calls == 1
    _, files2 = build(corpus, tmp_path / "out", client=build_client(StubProvider(), clock=FakeClock())["client"])
    assert files == files2  # mismo corpus + mismo LLM → mismo sitio


def test_cada_enlace_del_index_apunta_a_un_archivo_generado(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    out = tmp_path / "out"
    client = build_client(StubProvider(), clock=FakeClock())["client"]
    _, files = build(corpus, out, client=client)
    out.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():  # así escribe `docs-gen build`
        (out / name).write_text(content)

    enlaces = re.findall(r"\]\(([^)]+)\)", files["index.md"])
    assert enlaces, "el index debe enlazar cada ADR"
    assert sorted(enlaces) == sorted(name for name in files if name != "index.md")
    for enlace in enlaces:
        assert not enlace.startswith("/") and ".." not in enlace, enlace
        assert (out / enlace).is_file(), f"enlace roto en index.md: {enlace}"


def test_el_renderer_redacta_contenido_externo() -> None:
    secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    messages = render_docs_gen(
        "site-digest-generator",
        "0.1.0",
        {"date": "2026-10-15", "docs": "2", "est_tokens": "4000", "indice": secret},
    )
    user = messages[-1]["content"]
    assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in user
    assert "[REDACTED:" in user


def test_el_judge_evoluciona_el_dataset_completo() -> None:
    dataset = EvalDataset.from_jsonl(DEFAULT_DATASET)
    stub = StubProvider()
    client = build_client(stub, clock=FakeClock())["client"]
    out = run_evalset(dataset, judge_for(client), mode="full", threshold=1.0)
    assert out.total == 3
    assert out.passed == 3