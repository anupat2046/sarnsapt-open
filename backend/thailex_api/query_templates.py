from __future__ import annotations

import json
import re
from pathlib import Path


_PLACEHOLDER = re.compile(r"\{\{([A-Z0-9_]+)\}\}")
_HTTP_IRI = re.compile(r"^https?://[^\s<>\"{}|\\^`]+$")


def sparql_literal(value: str) -> str:
    """Encode a user value as a SPARQL string literal, never as query syntax."""
    return json.dumps(value, ensure_ascii=False)


def sparql_iri(value: str) -> str:
    if not _HTTP_IRI.fullmatch(value):
        raise ValueError("IRI must be an absolute HTTP(S) URI without unsafe characters")
    return f"<{value}>"


def sparql_int(value: int, *, minimum: int = 1, maximum: int = 500) -> str:
    if not minimum <= value <= maximum:
        raise ValueError(f"integer must be between {minimum} and {maximum}")
    return str(value)


class QueryTemplates:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or Path(__file__).resolve().parents[1] / "queries"

    def render(self, name: str, **values: str) -> str:
        template = (self.directory / f"{name}.rq").read_text(encoding="utf-8")
        expected = set(_PLACEHOLDER.findall(template))
        provided = set(values)
        if expected != provided:
            missing = expected - provided
            extra = provided - expected
            raise ValueError(f"template values mismatch: missing={missing}, extra={extra}")
        return _PLACEHOLDER.sub(lambda match: values[match.group(1)], template)
