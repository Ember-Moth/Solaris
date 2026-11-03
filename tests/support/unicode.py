"""Characters that exercise codepoint, UTF-16 and grapheme boundaries differently."""

# Deliberately decomposed accents and both presentation selectors, plus astral Han.
CLUSTERS = ("A", "中", "繁", "\U00020bb7", "e\u0301", "🇯🇵", "👩🏽‍💻", "👨‍👩‍👧‍👦", "✈︎", "✈️", "1️⃣")
TEXT = " / ".join(CLUSTERS)
