"""Split a document into retrievable passages.

Company knowledge is written as short sections under headings, so the headings
are the natural split: a passage keeps its heading with it, which both reads
better in a prompt and embeds better, because the heading carries the words a
customer is most likely to use ("subsidy", "warranty", "net metering").
"""

import re

#: Roughly the length of a passage that still answers one question on its own.
MAX_CHARS = 900

_HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)


def _split_long(heading: str, body: str) -> list[str]:
    """Break an over-long section on paragraph boundaries, keeping the heading."""
    parts: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", body):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > MAX_CHARS and current:
            parts.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        parts.append(current)
    return [f"{heading}\n\n{part}" if heading else part for part in parts]


def chunk_document(title: str, body: str) -> list[str]:
    """Return the passages of a document, each prefixed with its heading."""
    body = body.strip()
    if not body:
        return []

    matches = list(_HEADING.finditer(body))
    sections: list[tuple[str, str]] = []

    if not matches or matches[0].start() > 0:
        preamble = body[: matches[0].start()] if matches else body
        if preamble.strip():
            sections.append((title, preamble.strip()))

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        heading = match.group(1).strip()
        section = body[match.end() : end].strip()
        if section:
            # Keep the document title in the passage too: "Warranty" alone is
            # ambiguous once it is one row among hundreds.
            sections.append((f"{title} — {heading}", section))

    # One passage per section, however short. A two-line answer about warranty
    # is a better retrieval hit on its own than glued to the section above it.
    chunks: list[str] = []
    for heading, section in sections:
        chunks.extend(_split_long(heading, section))
    return chunks
