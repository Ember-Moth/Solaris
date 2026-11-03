"""Unicode boundaries for text summaries; original configuration is never shortened."""

from PySide6.QtCore import QTextBoundaryFinder


def utf8_prefix(text: str, max_bytes: int) -> str:
    """The longest prefix within a UTF-8 byte budget ending at a whole grapheme.

    Qt returns UTF-16 offsets, which cannot be used as Python string indices. Slice the
    UTF-16 representation at those offsets instead, retaining emoji sequences and marks.
    No GUI application or font database is needed, so background workers can use this.
    """
    if max_bytes <= 0:
        return ""
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, text)
    encoded = text.encode("utf-16-le")
    start = end = used = 0
    while (boundary := finder.toNextBoundary()) != -1:
        cluster = encoded[start * 2 : boundary * 2].decode("utf-16-le")
        used += len(cluster.encode("utf-8"))
        if used > max_bytes:
            break
        end = start = boundary
    return encoded[: end * 2].decode("utf-16-le")
