"""SkillPalette — a "/"-style quick switcher overlay.

A frameless, always-on-top picker: type to fuzzy-filter, ↑/↓ to move, Enter to
toggle a skill on/off (skills stack, so the palette stays open), Esc to close.
The currently-active skills are pinned first and marked. A footer row offers to
create a new skill. Unlike a status pill, this DOES take keyboard focus.

Theme-agnostic: all colours come from the active QPalette, so it blends into
whatever host it is dropped into. Wire `create_requested` to open your manager.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from skillkit.library import SkillLibrary
from skillkit.models import Skill

_CREATE_ROLE = Qt.ItemDataRole.UserRole + 1  # marks the "create new" footer row
_ID_ROLE = Qt.ItemDataRole.UserRole + 2


def _subsequence_score(query: str, text: str) -> float | None:
    """A small fuzzy matcher: every query char must appear in order. Rewards
    word-start hits and contiguous runs (fzy/Sublime-style). None = no match."""
    if not query:
        return 0.0
    query, text = query.lower(), text.lower()
    score = 0.0
    ti = 0
    prev_match = -2
    for qc in query:
        found = text.find(qc, ti)
        if found == -1:
            return None
        if found == prev_match + 1:
            score += 3.0  # contiguous
        if found == 0 or text[found - 1] in " -_/":
            score += 2.0  # word start
        score += 1.0
        prev_match = found
        ti = found + 1
    score -= 0.02 * len(text)  # prefer shorter, tighter matches
    return score


class SkillPalette(QWidget):
    create_requested = Signal()

    def __init__(
        self,
        library: SkillLibrary,
        *,
        changed_signal=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.Dialog,
        )
        self._library = library
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(460)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("skillPaletteCard")
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        self._header = QLabel()
        self._header.setObjectName("skillPaletteHeader")
        layout.addWidget(self._header)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter skills…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._rebuild)
        self._search.installEventFilter(self)
        layout.addWidget(self._search)

        self._list = QListWidget()
        self._list.setUniformItemSizes(True)
        self._list.setMaximumHeight(300)
        self._list.itemActivated.connect(self._activate_item)
        self._list.itemClicked.connect(self._activate_item)
        self._list.currentItemChanged.connect(lambda *_: self._update_detail())
        layout.addWidget(self._list)

        self._detail = QLabel()
        self._detail.setObjectName("skillPaletteDetail")
        self._detail.setWordWrap(True)
        layout.addWidget(self._detail)

        hint = QLabel("Enter toggle · ↑↓ move · Esc close")
        hint.setObjectName("skillPaletteHint")
        layout.addWidget(hint)

        self._apply_theme(card)
        if changed_signal is not None:
            changed_signal.connect(self._on_library_changed)

    # --- theming from the active palette ---

    def _apply_theme(self, card: QFrame) -> None:
        pal = self.palette()
        window = pal.color(pal.ColorRole.Window)
        text = pal.color(pal.ColorRole.WindowText)
        base = pal.color(pal.ColorRole.Base)
        highlight = pal.color(pal.ColorRole.Highlight)
        muted = pal.color(pal.ColorRole.PlaceholderText)
        bg = window.darker(112) if window.lightnessF() > 0.5 else window.lighter(112)
        card.setStyleSheet(
            f"""
            QFrame#skillPaletteCard {{
                background: rgba({bg.red()},{bg.green()},{bg.blue()},245);
                border: 1px solid rgba({text.red()},{text.green()},{text.blue()},40);
                border-radius: 12px;
            }}
            QLabel#skillPaletteHeader {{ font-weight: 600; color: {text.name()}; }}
            QLabel#skillPaletteDetail {{ color: {muted.name()}; font-size: 11px; }}
            QLabel#skillPaletteHint {{ color: {muted.name()}; font-size: 10px; }}
            QLineEdit {{
                background: {base.name()}; color: {text.name()};
                border: 1px solid rgba({text.red()},{text.green()},{text.blue()},50);
                border-radius: 6px; padding: 6px 8px;
            }}
            QListWidget {{
                background: transparent; color: {text.name()}; border: none; outline: none;
            }}
            QListWidget::item {{ padding: 6px 8px; border-radius: 6px; }}
            QListWidget::item:selected {{
                background: {highlight.name()}; color: white;
            }}
            """
        )

    # --- public API ---

    def popup(self) -> None:
        """Show centred on the screen under the cursor, focused and ready to type."""
        self._search.clear()
        self._rebuild("")
        self.adjustSize()
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(
                area.left() + (area.width() - self.width()) // 2,
                area.top() + (area.height() - self.height()) // 3,
            )
        self.show()
        self.raise_()
        self.activateWindow()
        self._search.setFocus()

    # --- list building ---

    def _on_library_changed(self) -> None:
        if self.isVisible():
            self._rebuild(self._search.text())
        self._update_header()

    def _rebuild(self, query: str = "") -> None:
        query = (query or "").strip()
        self._list.clear()
        skills = self._library.enabled_skills()
        active_ids = {s.id for s in self._library.active_skills()}

        if query:
            scored = []
            for skill in skills:
                hay = f"{skill.name} {skill.description} {' '.join(skill.triggers)}"
                score = _subsequence_score(query, hay)
                if score is not None:
                    scored.append((score, skill))
            scored.sort(key=lambda x: (-x[0], x[1].name.lower()))
            ordered = [s for _, s in scored]
        else:
            active = [s for s in skills if s.id in active_ids]
            rest = sorted(
                (s for s in skills if s.id not in active_ids), key=lambda s: s.name.lower()
            )
            ordered = active + rest

        for skill in ordered:
            self._add_row(skill, skill.id in active_ids)

        # footer: create-new (also the primary action when nothing matched)
        label = f"+ Create “{query}”" if (query and not ordered) else "+ Create a new skill…"
        item = QListWidgetItem(label)
        item.setData(_CREATE_ROLE, True)
        self._list.addItem(item)

        if self._list.count():
            self._list.setCurrentRow(0)
        self._update_header()
        self._update_detail()

    def _add_row(self, skill: Skill, active: bool) -> None:
        mark = "✓  " if active else "     "
        item = QListWidgetItem(f"{mark}{skill.name}")
        item.setData(_ID_ROLE, skill.id)
        self._list.addItem(item)

    def _update_header(self) -> None:
        active = self._library.active_skills()
        if not self._library.enabled:
            self._header.setText("Skills are turned off")
        elif active:
            self._header.setText("Active: " + " + ".join(s.name for s in active))
        else:
            self._header.setText("No skill active — pick one or more")

    def _update_detail(self) -> None:
        item = self._list.currentItem()
        if item is None or item.data(_CREATE_ROLE):
            self._detail.setText("Create a brand-new skill in the manager.")
            return
        skill = self._library.get(item.data(_ID_ROLE))
        self._detail.setText(skill.description or "No description." if skill else "")

    # --- actions ---

    def _activate_item(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        if item.data(_CREATE_ROLE):
            self.hide()
            self.create_requested.emit()
            return
        skill_id = item.data(_ID_ROLE)
        if skill_id:
            self._library.toggle(skill_id)  # stays open so several can be toggled

    def eventFilter(self, obj: QWidget, event: QEvent) -> bool:  # Qt override
        if obj is self._search and event.type() == QEvent.Type.KeyPress:
            assert isinstance(event, QKeyEvent)
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                row = self._list.currentRow()
                row += 1 if key == Qt.Key.Key_Down else -1
                self._list.setCurrentRow(max(0, min(self._list.count() - 1, row)))
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._activate_item(self._list.currentItem())
                return True
            if key == Qt.Key.Key_Escape:
                self.hide()
                return True
        return super().eventFilter(obj, event)

    def changeEvent(self, event: QEvent) -> None:  # Qt override
        # close when the palette loses focus (click elsewhere)
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self.hide()
        super().changeEvent(event)
