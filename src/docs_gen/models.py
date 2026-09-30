from pydantic import BaseModel


class AdrBlurb(BaseModel):
    """Resumen de una línea de un ADR (el único texto LLM por página)."""

    slug: str
    title: str
    blurb: str


class SiteDigest(BaseModel):
    """Narrativa del sitio: tagline + blurb por ADR. El resto del sitio sale del ParsedDocument."""

    tagline: str
    adrs: list[AdrBlurb]