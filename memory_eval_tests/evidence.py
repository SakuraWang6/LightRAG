"""Evidence-text helpers for product evaluation."""

# Characters dropped during evidence normalization.  The set intentionally
# includes JSON and Chinese punctuation so a rendered artifact (a table row
# serialised as ``["FACT-00007", "标准行标记", "200 次/秒"]`` or a sentence
# with 全角标点) normalises to the same token stream as the FACT-ID-anchored
# expected_text.  Formula and unit tokens (``× ÷ + - = / _ . %``) are kept.
_NORMALIZE_STRIP = frozenset("\\{} \"'\n,[]():;，。、：；！？「」“”‘’!?")


def normalize_evidence(text: str) -> str:
    return "".join(char.lower() for char in text if char not in _NORMALIZE_STRIP)


def normalize_evidence_offsets(content: str) -> tuple[str, list[int]]:
    """Normalize like :func:`normalize_evidence` while keeping source offsets."""
    normalized_chars: list[str] = []
    offsets: list[int] = []
    for index, char in enumerate(content):
        if char in _NORMALIZE_STRIP:
            continue
        normalized_chars.append(char.lower())
        offsets.append(index)
    return "".join(normalized_chars), offsets
