"""Identifiers that carry the product name, Solaris (docs/decisions.md D1).

src/core/internal/product/product.go mirrors these values.
"""

DISPLAY_NAME = "Solaris"  # also names the user data directory
PRODUCT_ID = "solaris"  # the subscription download's User-Agent

PIPE_NAME = r"\\.\pipe\ProtectedPrefix\Administrators\solaris"

PROTOCOL_VERSION = 1
FRAME_MAGIC = b"SLRS"
