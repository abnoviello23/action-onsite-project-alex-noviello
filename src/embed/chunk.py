"""Split a document into passages sized for BGE-small's 512-token window.

Pure functions: the service injects a tokenizer so this module does not load
the model. Short labels never reach here (`policy.worth_embedding`); everything
below assumes the caller already decided this is a document.

Slack messages that survive the policy are almost always one chunk. Notion
pages split on markdown headings, Drive docs on paragraphs, spreadsheets on
header + row batches — CSV split on sentences would destroy every row.
"""

from __future__ import annotations

from collections.abc import Callable

# Content tokens only. encode() still adds [CLS]/[SEP], so this stays well
# under the model's 512. A title prefix is reserved from this budget so it
# actually appears on every passage rather than being dropped when a chunk
# is already full.
MAX_TOKENS = 384
OVERLAP_TOKENS = 64
MAX_CHUNKS = 256

# Prefer keeping a heading with the section it introduces.
_MARKDOWN_SEPARATORS: tuple[str, ...] = (
    "\n# ",
    "\n## ",
    "\n### ",
    "\n\n",
    "\n",
    ". ",
    " ",
    "",
)

TokenCount = Callable[[str], int]


def chunk_document(
    content: str,
    count: TokenCount,
    *,
    title: str | None = None,
) -> list[str]:
    """Return passage texts, each <= MAX_TOKENS including the title prefix."""
    text = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []

    prefix = (title or "").strip()
    if prefix and _starts_with_title(text, prefix):
        prefix = ""
    header = f"{prefix}\n\n" if prefix else ""
    overhead = count(header) if header else 0
    if overhead >= MAX_TOKENS - OVERLAP_TOKENS:
        header = ""
        overhead = 0
    limit = MAX_TOKENS - overhead

    raw = (
        _split_csv(text, count, limit)
        if _looks_like_csv(text)
        else _split_prose(text, count, limit)
    )
    return [
        _clamp(header + piece, count, MAX_TOKENS)
        for piece in raw
        if piece.strip()
    ][:MAX_CHUNKS]


def _starts_with_title(text: str, title: str) -> bool:
    first = text.split("\n", 1)[0].strip()
    return first == title


def _clamp(text: str, count: TokenCount, limit: int) -> str:
    text = text.strip()
    if count(text) <= limit:
        return text
    return _fit_prefix(text, count, limit).strip()


def _looks_like_csv(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if lines and "," not in lines[0]:
        lines = lines[1:]
    sample = lines[:8]
    if len(sample) < 3:
        return False
    counts = [ln.count(",") for ln in sample]
    if min(counts) < 2:
        return False
    return max(counts) - min(counts) <= 2


def _split_csv(text: str, count: TokenCount, limit: int) -> list[str]:
    lines = text.splitlines()
    i = 0
    while i < len(lines) and "," not in lines[i]:
        i += 1
    rest = lines[i:]
    if len(rest) < 2:
        return _split_prose(text, count, limit)

    header = rest[0]
    rows = rest[1:]
    lead = [ln for ln in lines[:i] if ln.strip()]
    lead.append(header)
    prefix = "\n".join(lead)

    chunks: list[str] = []
    batch: list[str] = []
    for row in rows:
        candidate = batch + [row]
        rendered = prefix + "\n" + "\n".join(candidate)
        if batch and count(rendered) > limit:
            chunks.append(prefix + "\n" + "\n".join(batch))
            overlap = batch[-1:]
            batch = overlap + [row]
            if count(prefix + "\n" + "\n".join(batch)) > limit:
                chunks.extend(_hard_pieces(prefix + "\n" + row, count, limit))
                batch = []
        else:
            batch = candidate
    if batch:
        chunks.append(prefix + "\n" + "\n".join(batch))
    return chunks


def _split_prose(text: str, count: TokenCount, limit: int) -> list[str]:
    if count(text) <= limit:
        return [text]
    units = _split_units(text, count, _MARKDOWN_SEPARATORS, limit)
    return _pack(units, count, limit)


def _split_units(
    text: str, count: TokenCount, seps: tuple[str, ...], limit: int
) -> list[str]:
    if not text.strip():
        return []
    if count(text) <= limit:
        return [text]
    if not seps or seps[0] == "":
        return _hard_pieces(text, count, limit)

    sep, rest = seps[0], seps[1:]
    pieces = _cut(text, sep)
    out: list[str] = []
    for piece in pieces:
        if not piece.strip():
            continue
        if count(piece) <= limit:
            out.append(piece)
        else:
            out.extend(_split_units(piece, count, rest, limit))
    return out


def _cut(text: str, sep: str) -> list[str]:
    parts = text.split(sep)
    if len(parts) == 1:
        return parts
    # Keep the heading/separator on the section it belongs to, not stranded on
    # the previous chunk's tail.
    attached = sep[1:] if sep.startswith("\n") else sep
    out = [parts[0]]
    out.extend(attached + part for part in parts[1:])
    return out


def _pack(units: list[str], count: TokenCount, limit: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n{unit}" if current else unit
        if not current or count(candidate) <= limit:
            current = candidate
            continue
        chunks.append(current)
        overlap = _overlap_tail(current, count)
        nxt = f"{overlap}\n{unit}".strip() if overlap else unit
        if count(nxt) <= limit:
            current = nxt
            continue
        chunks.extend(_hard_pieces(unit, count, limit))
        current = ""
    if current.strip():
        chunks.append(current)
    return chunks


def _overlap_tail(text: str, count: TokenCount) -> str:
    if OVERLAP_TOKENS <= 0 or not text:
        return ""
    tail = text
    while count(tail) > OVERLAP_TOKENS and "\n" in tail:
        tail = tail.split("\n", 1)[-1]
    if count(tail) > OVERLAP_TOKENS:
        tail = _fit_prefix_from_end(tail, count, OVERLAP_TOKENS)
    return tail.strip()


def _hard_pieces(text: str, count: TokenCount, limit: int) -> list[str]:
    pieces: list[str] = []
    rest = text
    while rest:
        if count(rest) <= limit:
            pieces.append(rest)
            break
        head = _fit_prefix(rest, count, limit)
        if not head:
            break
        pieces.append(head)
        rest = rest[len(head) :].lstrip()
    return pieces


def _fit_prefix(text: str, count: TokenCount, budget: int) -> str:
    if count(text) <= budget:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if count(text[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    cut = text[:lo].rfind(" ")
    return text[:cut] if cut > 0 else text[:lo]


def _fit_prefix_from_end(text: str, count: TokenCount, budget: int) -> str:
    if count(text) <= budget:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi) // 2
        if count(text[mid:]) <= budget:
            hi = mid
        else:
            lo = mid + 1
    start = text[lo:].find(" ")
    piece = text[lo + start + 1 :] if start != -1 else text[lo:]
    return piece or text[lo:]
