"""Tools settings tab — manage the capabilities the local LLM may execute.

Master switches (feature, per-call confirmation, web access with a prominent
risk warning), one row per installed tool with its own enable toggle, and
import/export of tools as zip files — the same exchange story as skills.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cleanwispr.ui import theme
from cleanwispr.ui.widgets import LabeledToggle, ToggleSwitch, intro_label
from toolkit.library import ToolError, ToolLibrary
from toolkit.models import ToolSpec

_WEB_WARNING = (
    "<b>⚠️ Read this before enabling web access.</b><br>"
    "Anything a tool fetches from the internet becomes part of the model's input. "
    "A web page can contain <b>hidden instructions (prompt injection)</b> that try to "
    "hijack the model — for example telling it to run other tools, rewrite your text "
    "maliciously, or leak whatever is in the current request (your dictated command "
    "and any selected text) to a URL the page controls. Small local models follow "
    "such planted instructions far more readily than big cloud models.<br><br>"
    "Only enable web access if you understand this risk. Keep "
    "<i>“ask before every tool call”</i> on if you want to see and approve each "
    "request, and never enable tools from sources you don't trust. Fetching also "
    "reveals your IP address to the sites the model chooses."
)


class ToolsTab(QWidget):
    def __init__(self, library: ToolLibrary) -> None:
        super().__init__()
        self._library = library

        layout = QVBoxLayout(self)
        layout.addWidget(
            intro_label(
                "Tools are small Python capabilities the voice editor's LLM can run "
                "while answering — fetch a web page, run a calculation, or create new "
                "tools when you ask it to. Skills shape HOW the model writes; tools are "
                "WHAT it can do. Exchange tools as .zip files, or ask the model to "
                "build one (new tools stay disabled until you enable them here)."
            )
        )
        layout.addWidget(self._switches_group())
        layout.addWidget(self._web_group())
        self._tools_group = QGroupBox("Installed tools")
        self._tools_box = QVBoxLayout(self._tools_group)
        layout.addWidget(self._tools_group)
        layout.addLayout(self._buttons_row())
        layout.addStretch()
        self._rebuild_rows()

    # --- master switches ---

    def _switches_group(self) -> QGroupBox:
        group = QGroupBox("Tool use")
        box = QVBoxLayout(group)

        enabled = LabeledToggle("Let the model use tools")
        enabled.setChecked(self._library.config.enabled)
        enabled.toggled.connect(lambda on: self._library.set_config(enabled=on))
        box.addWidget(enabled)

        confirm = LabeledToggle("Ask before every tool call")
        confirm.setToolTip(
            "Off: only tools marked as sensitive (like Run Python) ask for "
            "permission. On: every single call shows a confirmation dialog first."
        )
        confirm.setChecked(self._library.config.confirm_all)
        confirm.toggled.connect(lambda on: self._library.set_config(confirm_all=on))
        box.addWidget(confirm)
        return group

    def _web_group(self) -> QGroupBox:
        group = QGroupBox("Web access")
        box = QVBoxLayout(group)

        toggle = LabeledToggle("Allow tools that access the internet")
        toggle.setChecked(self._library.config.allow_network)
        toggle.toggled.connect(lambda on: self._library.set_config(allow_network=on))
        box.addWidget(toggle)

        warning = QLabel(_WEB_WARNING)
        warning.setWordWrap(True)
        warning.setStyleSheet(
            "QLabel { color: #f5a524; background: rgba(245, 165, 36, 0.08); "
            "border: 1px solid rgba(245, 165, 36, 0.45); border-radius: 6px; "
            "padding: 10px; }"
        )
        box.addWidget(warning)
        return group

    # --- tool rows ---

    def refresh(self) -> None:
        self._library.refresh()
        self._rebuild_rows()

    def _rebuild_rows(self) -> None:
        while self._tools_box.count():
            item = self._tools_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        specs = self._library.all()
        if not specs:
            empty = QLabel("No tools installed — import a .zip or ask the model to create one.")
            empty.setStyleSheet(f"color: {theme.MUTED};")
            self._tools_box.addWidget(empty)
            return
        for spec in specs:
            self._tools_box.addWidget(self._tool_row(spec))

    def _tool_row(self, spec: ToolSpec) -> QWidget:
        row = QFrame()
        row.setStyleSheet(
            f"QFrame {{ background: {theme.SURFACE_2}; border-radius: 6px; }}"
        )
        box = QHBoxLayout(row)
        box.setContentsMargins(10, 6, 10, 6)

        toggle = ToggleSwitch(spec.enabled)
        toggle.toggled.connect(lambda on, tool_id=spec.id: self._library.set_enabled(tool_id, on))
        box.addWidget(toggle)

        badges = []
        if spec.builtin:
            badges.append("built-in")
        if spec.network:
            badges.append("🌐 web")
        if spec.confirm:
            badges.append("asks first")
        badge_text = (
            f"  <span style='color:{theme.MUTED};'>({', '.join(badges)})</span>"
            if badges
            else ""
        )
        label = QLabel(
            f"<b>{spec.name}</b>{badge_text}<br>"
            f"<span style='color:{theme.MUTED};'>{spec.description[:160]}</span>"
        )
        label.setWordWrap(True)
        box.addWidget(label, 1)

        export = QPushButton("Export")
        export.setToolTip("Save this tool as a .zip to share or back up")
        export.clicked.connect(lambda _=False, tool_id=spec.id: self._export(tool_id))
        box.addWidget(export)

        if not spec.builtin:
            delete = QPushButton("Delete")
            delete.setObjectName("danger")
            delete.clicked.connect(lambda _=False, tool_id=spec.id: self._delete(tool_id))
            box.addWidget(delete)
        return row

    # --- actions ---

    def _buttons_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        import_button = QPushButton("Import tool (.zip)…")
        import_button.clicked.connect(self._import)
        row.addWidget(import_button)
        folder_button = QPushButton("Open tools folder")
        folder_button.setToolTip(str(self._library.root))
        folder_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._library.root)))
        )
        row.addWidget(folder_button)
        row.addStretch()
        return row

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import tool", "", "Tool zip (*.zip)")
        if not path:
            return
        try:
            spec = self._library.import_zip(Path(path))
        except ToolError as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return
        self._rebuild_rows()
        QMessageBox.information(
            self,
            "Tool imported",
            f"“{spec.name}” was imported and is DISABLED.\n\n"
            "Review its code (Open tools folder) and flip its switch when "
            "you're happy — imported code never runs before you enable it.",
        )

    def _export(self, tool_id: str) -> None:
        spec = self._library.get(tool_id)
        if spec is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export tool", f"{spec.id}.zip", "Tool zip (*.zip)"
        )
        if not path:
            return
        try:
            self._library.export_zip(tool_id, Path(path))
        except (ToolError, OSError) as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    def _delete(self, tool_id: str) -> None:
        spec = self._library.get(tool_id)
        if spec is None:
            return
        reply = QMessageBox.question(
            self,
            "Delete tool",
            f"Delete “{spec.name}” and its files? This cannot be undone.",
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self._library.remove(tool_id)
            except ToolError as exc:
                QMessageBox.warning(self, "Delete failed", str(exc))
            self._rebuild_rows()
