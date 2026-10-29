"""Identifiers that change with the product name.

"mihomo-desktop" is a working name until the product is named (docs/architecture.md §12);
src/core/internal/product/product.go mirrors these values.
"""

DISPLAY_NAME = "Mihomo Desktop"

PIPE_NAME = r"\\.\pipe\ProtectedPrefix\Administrators\mihomo-desktop"

PROTOCOL_VERSION = 1
FRAME_MAGIC = b"MDCP"
