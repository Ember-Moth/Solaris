import os

# Qt renders headless in tests; set before PySide6 creates the application.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
