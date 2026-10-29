"""Identifiers that change with the product name.

"mihomo-desktop" is a working name until the product is named (docs/architecture.md §12);
src/core/internal/product/product.go mirrors these values.
"""

DISPLAY_NAME = "Mihomo Desktop"  # also names the user data directory
PRODUCT_ID = "mihomo-desktop"  # the subscription download's User-Agent

PIPE_NAME = r"\\.\pipe\ProtectedPrefix\Administrators\mihomo-desktop"

PROTOCOL_VERSION = 1
FRAME_MAGIC = b"MDCP"
