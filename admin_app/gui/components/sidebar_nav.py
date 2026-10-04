"""Admin app's sidebar navigation - the persistent left rail every one of
the 9 mockup pages shares (brand header, grouped nav links, a pinned
account footer). Recreated from the mockup's <aside> markup (identical
across all 9 page files) rather than copied HTML - see icons.py for the
one piece (icon path data) that IS copied verbatim, since redrawing an
icon by hand risks it not matching.

Deliberately admin_app-only, not shared/gui_kit - same reasoning as
compact_button.py: the MECHANISM (a checkable button list wired to a
signal) is generic, but this widget's sizing, grouping, and the
Classical palette it's styled with are this app's own.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from shared.i18n import tr
from admin_app.theme import CLASSICAL_PALETTE, FONT_HEADING_CSS
from shared.gui_kit.icon_kit import svg_to_icon
from admin_app.gui.motion import Level, blend

SIDEBAR_WIDTH_PX = 232


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    icon_path: str
    badge: str | None = None


@dataclass(frozen=True)
class NavSection:
    title: str
    items: list[NavItem] = field(default_factory=list)


class _NavButton(QPushButton):
    """A nav row: hover tint, then an accent bar that grows when it becomes
    the current page (drawn here so it can animate; a stylesheet can't)."""

    def __init__(self, text: str):
        super().__init__(text)
        self.setCheckable(True)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self.setMinimumHeight(33)
        self._on = Level(self, lambda _v: self.update(), 220, 0.0)
        self._hover = Level(self, lambda _v: self.update(), 140)
        self.toggled.connect(lambda on: self._on.go(1.0 if on else 0.0))

    def event(self, event) -> bool:
        if event.type() in (QEvent.Enter, QEvent.HoverEnter):
            self._hover.go(1.0)
        elif event.type() in (QEvent.Leave, QEvent.HoverLeave):
            self._hover.go(0.0)
        return super().event(event)

    def paintEvent(self, _event) -> None:
        p = CLASSICAL_PALETTE
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        on, hover = self._on.value, self._hover.value
        tint = QColor(234, 231, 231, round(13 * hover * (1 - on)))
        if on > 0:
            tint = QColor(225, 173, 102, round(18 * on))
        painter.fillRect(self.rect(), tint)
        if on > 0.01:
            height = self.height() * on
            painter.fillRect(QRectF(0, (self.height() - height) / 2, 2, height), QColor(p["accent"]))
        colour = QColor(blend(blend(p["text_secondary"], p["text_primary"], hover), p["accent"], on))
        shift = 2 * on + 1 * hover * (1 - on)  # the label leans in a little
        icon_size = self.iconSize().width()
        icon_rect = QRectF(10 + shift, (self.height() - icon_size) / 2, icon_size, icon_size)
        icon = self.icon()
        if not icon.isNull():
            icon.paint(painter, icon_rect.toRect())
        text_left = 10 + icon_size + 8 + shift
        painter.setPen(colour)
        painter.setFont(self.font())
        text = self.text().replace("&&", "&")
        painter.drawText(
            QRectF(text_left, 0, self.width() - text_left - 6, self.height()),
            Qt.AlignLeft | Qt.AlignVCenter,
            self.fontMetrics().elidedText(text, Qt.ElideRight, int(self.width() - text_left - 6)),
        )


class SidebarNav(QWidget):
    """Emits `page_selected(key)` when the user clicks a nav item.

    Exactly one item is checked/active at a time (a single QButtonGroup
    spans every section) - matches the mockup's single-active-page look,
    where the current page gets an accent left-edge bar and tinted
    background (see the stylesheet below).
    """

    page_selected = Signal(str)
    sign_out_requested = Signal()

    def __init__(
        self,
        sections: list[NavSection],
        brand_title: str,
        brand_subtitle: str,
        user_name: str,
        user_role: str,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setFixedWidth(SIDEBAR_WIDTH_PX)
        p = CLASSICAL_PALETTE

        self.setStyleSheet(
            f"""
            SidebarNav {{
                background-color: {p['background']};
                border-right: 1px solid {p['border']};
            }}
            """
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_header(brand_title, brand_subtitle))

        nav_area = QWidget()
        nav_layout = QVBoxLayout(nav_area)
        nav_layout.setContentsMargins(8, 0, 8, 0)
        nav_layout.setSpacing(1)

        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        self._buttons_by_key: dict[str, QPushButton] = {}

        for section_index, section in enumerate(sections):
            nav_layout.addWidget(self._build_section_label(section.title, first=section_index == 0))
            for item in section.items:
                button = self._build_item_button(item)
                nav_layout.addWidget(button)
                self._button_group.addButton(button)
                self._buttons_by_key[item.key] = button

        nav_layout.addStretch()
        outer.addWidget(nav_area, stretch=1)

        outer.addWidget(self._build_footer(user_name, user_role))

        if self._buttons_by_key:
            first_key = next(iter(self._buttons_by_key))
            self._buttons_by_key[first_key].setChecked(True)

    def set_active(self, key: str) -> None:
        """Sync the highlighted item without emitting page_selected again."""
        button = self._buttons_by_key.get(key)
        if button is not None:
            button.setChecked(True)

    def _build_header(self, brand_title: str, brand_subtitle: str) -> QWidget:
        p = CLASSICAL_PALETTE
        header = QWidget()
        layout = QVBoxLayout(header)
        layout.setContentsMargins(18, 18, 18, 26)
        layout.setSpacing(2)

        title = QLabel(brand_title)
        title.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 22px; color: {p['text_primary']};")
        layout.addWidget(title)

        subtitle = QLabel(brand_subtitle.upper())
        subtitle.setStyleSheet(f"font-size: 10px; letter-spacing: 2px; color: {p['text_secondary']};")
        layout.addWidget(subtitle)
        return header

    def _build_section_label(self, title: str, first: bool) -> QLabel:
        p = CLASSICAL_PALETTE
        label = QLabel(title.upper())
        top_margin = 4 if first else 22
        label.setContentsMargins(8, top_margin, 8, 4)
        label.setStyleSheet(f"font-size: 10px; letter-spacing: 1.5px; color: {p['text_secondary']};")
        return label

    def _build_item_button(self, item: NavItem) -> QPushButton:
        p = CLASSICAL_PALETTE
        # "&&": a lone "&" in a QPushButton label is a mnemonic marker, so
        # "Treasury & Ledger" used to render as "Treasury  Ledger".
        button = _NavButton(self._button_text(item.label, item.badge))
        button.setProperty("nav_label", item.label)
        button.setCursor(Qt.PointingHandCursor)
        button.setIcon(svg_to_icon(item.icon_path, p["text_secondary"], size=16))
        button.setIconSize(button.iconSize())
        button.setLayoutDirection(Qt.LeftToRight)
        button.setFlat(True)
        button.clicked.connect(lambda checked, k=item.key: self._on_clicked(k))
        return button

    @staticmethod
    def _button_text(label: str, badge: str | None) -> str:
        text = label.replace("&", "&&")
        return f"{text}    {badge}" if badge else text

    def set_badge(self, key: str, badge: str | None) -> None:
        """Update a nav item's badge text live (e.g. the Purchase requests
        pending count) - None or "" hides it."""
        button = self._buttons_by_key.get(key)
        if button is None:
            return
        label = button.property("nav_label")
        button.setText(self._button_text(label, badge or None))

    def _on_clicked(self, key: str) -> None:
        self.page_selected.emit(key)

    def _build_footer(self, user_name: str, user_role: str) -> QWidget:
        p = CLASSICAL_PALETTE
        footer = QFrame()
        # Scoped by object name: a selector-less stylesheet cascades to every
        # child label, drawing a stray line/box around each of them.
        footer.setObjectName("sidebarFooter")
        footer.setAttribute(Qt.WA_StyledBackground, True)
        footer.setStyleSheet(f"#sidebarFooter {{ border-top: 1px solid {p['border']}; }}")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(10)

        initials = "".join(part[0] for part in user_name.split()[:2]).upper() or "?"
        avatar = QLabel(initials)
        self._avatar_label = avatar
        avatar.setFixedSize(30, 30)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet(
            f"""
            border: 1px solid {p['accent']};
            border-radius: 15px;
            color: {p['accent']};
            font-family: {FONT_HEADING_CSS};
            font-size: 14px;
            """
        )
        layout.addWidget(avatar)

        names = QVBoxLayout()
        names.setSpacing(0)
        name_label = QLabel(user_name)
        self._user_name_label = name_label
        name_label.setStyleSheet(f"color: {p['text_primary']}; font-size: 13px;")
        role_label = QLabel(user_role)
        self._user_role_label = role_label
        role_label.setStyleSheet(f"color: {p['text_secondary']}; font-size: 11px;")
        names.addWidget(name_label)
        names.addWidget(role_label)

        names_widget = QWidget()
        names_widget.setLayout(names)
        layout.addWidget(names_widget, stretch=1)

        # The signed-in administrator can sign out (Admin then asks for a
        # sign-in again before showing anything). A small link under the
        # role, so the name and role keep the footer's full width.
        self.sign_out_button = QPushButton(tr("admin.shell.sign_out"))
        self.sign_out_button.setCursor(Qt.PointingHandCursor)
        self.sign_out_button.setFlat(True)
        self.sign_out_button.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {p['accent']}; font-size: 11px; "
            f"padding: 2px 0; text-align: left; }}"
            f"QPushButton:hover {{ text-decoration: underline; }}"
        )
        self.sign_out_button.clicked.connect(self.sign_out_requested.emit)
        names.addWidget(self.sign_out_button)
        return footer

    def set_user(self, user_name: str, user_role: str) -> None:
        """Show who is signed in (after a sign-in or a change of person)."""
        self._avatar_label.setText("".join(part[0] for part in user_name.split()[:2]).upper() or "?")
        self._user_name_label.setText(user_name)
        self._user_role_label.setText(user_role)
