"""Application-wide dark theme.

Primary: qt-material's dark_purple — a complete Material Design stylesheet
covering every Qt widget — plus a small override layer for app-specific bits.
Fallback: the hand-rolled QSS below, used if qt-material is unavailable.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

ACCENT = "#7c66dc"
BG = "#131316"
SURFACE = "#1b1b1f"
SURFACE_2 = "#232329"
BORDER = "#2e2e36"
TEXT = "#e4e2ea"
MUTED = "#8a8f98"
DANGER = "#e5484d"

_QSS = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-size: 12px; }}
QLabel {{ background: transparent; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 6px; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {MUTED}; padding: 8px 16px;
    border: none; border-bottom: 2px solid transparent; margin-right: 2px;
}}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}

QGroupBox {{
    border: 1px solid {BORDER}; border-radius: 8px; margin-top: 12px;
    padding-top: 8px; background: {SURFACE};
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {MUTED};
    font-weight: bold;
}}

QPushButton {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 6px 14px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {BG}; }}
QPushButton:disabled {{ color: {MUTED}; background: {SURFACE}; }}
QPushButton#danger {{ color: {DANGER}; }}
QPushButton#danger:hover {{ border-color: {DANGER}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 5px 8px; selection-background-color: {ACCENT};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}

QComboBox {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 6px 10px;
}}
QComboBox:hover {{ border-color: {ACCENT}; }}
QComboBox:on {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url("{{ARROW}}"); width: 12px; height: 12px; }}
QComboBox::down-arrow:on {{ top: 1px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px; outline: 0;
}}
QComboBox QAbstractItemView::item {{
    padding: 7px 12px; border-radius: 5px; min-height: 20px;
}}
QComboBox QAbstractItemView::item:hover {{ background: {BG}; }}
QComboBox QAbstractItemView::item:selected {{ background: {ACCENT}; color: white; }}

QProgressBar {{
    background: {SURFACE_2}; border: none; border-radius: 5px; height: 10px;
    text-align: center;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}

QTableWidget {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px;
    gridline-color: transparent; alternate-background-color: {SURFACE_2};
}}
QTableWidget::item {{ padding: 6px; border: none; }}
QTableWidget::item:selected {{ background: {ACCENT}; color: white; }}
QHeaderView::section {{
    background: {SURFACE}; color: {MUTED}; border: none;
    border-bottom: 1px solid {BORDER}; padding: 6px; font-weight: bold;
}}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {BORDER}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: {BORDER}; border-radius: 5px; min-width: 30px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

QSplitter::handle {{ background: {BORDER}; width: 2px; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 24px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT}; }}
QMessageBox {{ background: {SURFACE}; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; }}
"""


# app-specific tweaks layered over qt-material
_MATERIAL_EXTRA_QSS = f"""
/* replace qt-material's pink squared buttons with the app's rounded look */
QPushButton {{
    background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 5px 16px; font-weight: 600;
}}
QPushButton:hover {{
    border-color: {ACCENT}; background: rgba(124, 102, 220, 0.10); color: {TEXT};
}}
QPushButton:pressed {{ background: {BG}; }}
QPushButton:disabled {{ color: {MUTED}; background: {SURFACE}; border-color: {BORDER}; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton#danger {{ color: {DANGER}; border-color: rgba(229, 72, 77, 0.5); }}
QPushButton#danger:hover {{ background: rgba(229, 72, 77, 0.15); border-color: {DANGER}; }}
QGroupBox {{ font-size: 12px; }}
QTableWidget {{ alternate-background-color: {SURFACE_2}; }}
QToolTip {{
    background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER};
    padding: 6px; border-radius: 4px;
}}
"""


def _apply_material(app: QApplication) -> bool:
    try:
        from qt_material import apply_stylesheet
    except ImportError:
        return False
    apply_stylesheet(
        app,
        theme="dark_purple.xml",
        extra={
            "density_scale": "-1",  # slightly tighter than Material default
            "font_size": "13px",
        },
    )
    app.setStyleSheet(app.styleSheet() + _MATERIAL_EXTRA_QSS)
    return True


def _arrow_icon_path() -> str:
    """Draw the dropdown chevron once into the cache (QSS needs a file url)."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

    from cleanwispr.storage import paths

    target = paths.cache_dir() / "ui" / "arrow-down.png"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(MUTED))
        pen.setWidthF(2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline([QPointF(6, 9.5), QPointF(12, 15.5), QPointF(18, 9.5)])
        painter.end()
        pixmap.save(str(target), "PNG")
    return target.as_posix()


def apply(app: QApplication) -> None:
    if _apply_material(app):
        return
    # fallback: hand-rolled dark QSS
    app.setStyle("Fusion")
    app.setStyleSheet(_QSS.replace("{ARROW}", _arrow_icon_path()))
