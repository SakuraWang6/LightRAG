"""Evidence-text helpers for product evaluation."""

def normalize_evidence(text: str) -> str:
    return (
        text.replace("\\", "")
        .replace("{", "")
        .replace("}", "")
        .replace(" ", "")
        .replace("\n", "")
        .lower()
    )
