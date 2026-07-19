"""Small shared UI helpers."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel


def intro_label(text: str) -> QLabel:
    """Muted, wrapped explainer shown at the top of a settings tab."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: #8a8f98; padding: 2px 2px 8px 2px;")
    return label
