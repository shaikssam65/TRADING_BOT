from __future__ import annotations


def to_bullets(*parts: object, limit: int = 4) -> list[str]:
    """Turn text or lists into at most `limit` short bullet lines."""
    items: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if part is None:
            continue
        if isinstance(part, list):
            chunks = [str(x) for x in part]
        else:
            text = str(part).replace("•", "\n").replace(";", "\n")
            chunks = text.split("\n")
        for raw in chunks:
            line = raw.strip().lstrip("-*").strip()
            if not line:
                continue
            key = line.lower()
            if key in seen:
                continue
            seen.add(key)
            items.append(line[:160])
            if len(items) >= limit:
                return items
    return items


def bullets_md(items: list[str] | None, fallback: str = "") -> str:
    lines = to_bullets(items or [], fallback, limit=4)
    if not lines:
        return ""
    return "\n".join(f"- {line}" for line in lines)
