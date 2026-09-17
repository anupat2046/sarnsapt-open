from __future__ import annotations

from fastapi.testclient import TestClient

from thailex_api.main import create_app


def main() -> None:
    with TestClient(create_app()) as client:
        health = client.get("/health")
        health.raise_for_status()

        search = client.get("/api/search", params={"q": "ขัน", "limit": 5})
        search.raise_for_status()
        assert search.json()["results"], "search returned no results"

        answer = client.post(
            "/api/ask",
            json={
                "query": "คำว่า ขัน ในประโยค พ่อขันนอตให้แน่น หมายถึงอะไร",
                "hops": 2,
                "max_candidates": 8,
                "use_llm": False,
            },
        )
        answer.raise_for_status()
        payload = answer.json()
        assert payload["candidates"], "ask returned no candidates"
        assert payload["selection"]["selected_sense_uri"], "no sense selected"
        assert payload["citations"], "selected sense has no citations"
        assert payload["evidence_validated"] is True
        print(
            "Phase 4 smoke test passed:",
            payload["detected_lemma"],
            payload["selection"]["selected_sense_uri"],
            f"citations={len(payload['citations'])}",
            f"edges={len(payload['graph']['edges'])}",
        )


if __name__ == "__main__":
    main()
