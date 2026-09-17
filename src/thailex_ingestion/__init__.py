"""Data-ingestion utilities for the ThaiLex knowledge graph."""

from .lexitron import LexitronAdapter, build_lexitron_subset, records_to_turtle
from .wordnet_lmf import WordNetLMFParser, build_omw_full_streaming, build_omw_subset
from .organizer import (
    OrganizerCSVAdapter,
    OrganizerJSONAdapter,
    OrganizerXMLAdapter,
    audit_dataset,
    build_dataset,
)
from .alignment import (
    GraphSenseProvider,
    apply_decisions,
    build_alignment,
    generate_candidates,
    select_review_batch,
)

__all__ = [
    "LexitronAdapter",
    "WordNetLMFParser",
    "build_lexitron_subset",
    "build_omw_subset",
    "build_omw_full_streaming",
    "OrganizerCSVAdapter",
    "OrganizerJSONAdapter",
    "OrganizerXMLAdapter",
    "audit_dataset",
    "build_dataset",
    "GraphSenseProvider",
    "apply_decisions",
    "build_alignment",
    "generate_candidates",
    "select_review_batch",
    "records_to_turtle",
]
