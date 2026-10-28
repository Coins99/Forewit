"""Fusion style, palette and stylesheet built from the design tokens."""

import contextlib
import dataclasses
import gc
import string
from collections.abc import Callable, Iterator
from typing import Final, cast

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QProxyStyle,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleFactory,
    QStyleHintReturn,
    QStyleOption,
    QStyleOptionButton,
    QStyleOptionComboBox,
    QStyleOptionComplex,
    QStyleOptionSpinBox,
    QWidget,
)

from mailbrief.ui.theme.assets import (
    register_fonts,
    release_font_cache,
    release_icon_cache,
    ui_font,
)
from mailbrief.ui.theme.indicators import (
    BOX_RADIUS,
    indicator_colors,
    paint_check_box,
    paint_radio,
)
from mailbrief.ui.theme.tokens import (
    DARK,
    DEFAULT_SCHEME,
    DIALOG_MARGIN,
    FIELD_RADIUS,
    ITEM_PAD_X,
    ITEM_PAD_Y,
    MENU_ITEM_RADIUS,
    PILL_RADIUS,
    RADIUS,
    TAB_RADIUS,
    TEXT_PX,
    ColorScheme,
    GlassTokens,
    ThemeMode,
    Tokens,
    glass_for,
    tokens_for,
)

QSS = """\
QWidget#workspace { background: $panel; }
QWidget#headerBar, QWidget#sidebar, QWidget#contentSheet, QWidget#footerBar, QListView#sidebarNav { background: transparent; border: none; outline: 0; padding: 0; }
QLabel[tone="secondary"] { color: $text_secondary; }
QLabel[tone="muted"] { color: $text_muted; }
QWidget#sidebar QLabel[tone="muted"] { color: $text_secondary; }
QLabel[tone="warning"] { color: $warning_fg; }
QPushButton[variant="nav"] { text-align: left; background: transparent; color: $text_secondary; border: none; border-radius: ${radius}px; padding: 6px 10px; }
QPushButton[variant="nav"]:hover, QPushButton[variant="nav"]:focus { background: $selection; color: $text; }
QListView { background: $panel; border: 1px solid $hairline; border-radius: ${radius}px; padding: 3px; }
QListView[frameShape="NoFrame"] { background: transparent; border: none; padding: 0; }
QComboBox QListView, QComboBox QListView[frameShape="NoFrame"] { background: transparent; border: none; padding: 4px; outline: 0; color: $text; }
QComboBox QListView::item { padding: 4px 8px; border-radius: ${menu_item_radius}px; }
QComboBox QListView::item:selected { background: $selection; color: $text; }
QMenu { background: $panel; color: $text; border: 1px solid $hairline; border-radius: ${radius}px; padding: 4px; }
QMenu::item { background: transparent; padding: 5px 20px 5px 12px; border-radius: ${menu_item_radius}px; }
QMenu::item:selected { background: $selection; color: $text; }
QMenu::item:disabled { color: $text_muted; }
QMenu::separator { height: 1px; background: $hairline; margin: 4px 8px; }
QToolTip { background: $panel; color: $text; border: 1px solid $hairline; border-radius: ${menu_item_radius}px; padding: 4px 8px; }
QLineEdit, QPlainTextEdit, QTextEdit { background: $panel; color: $text; border: 1px solid $hairline; border-radius: ${field_radius}px; padding: 4px 8px; selection-background-color: $primary_fill; selection-color: $on_primary; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus { border-color: $accent_fg; }
QLineEdit#paletteField { border-color: $border_strong; border-radius: ${pill_radius}px; padding: 6px 14px; }
QLineEdit#paletteField:focus { border-color: $accent_fg; }
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled { color: $text_muted; }
QComboBox QLineEdit, QAbstractSpinBox QLineEdit { border: none; border-radius: 0; padding: 0; background: transparent; }
QListView#briefList, QListView#actionList, QListView#inboxList { background: $panel; border: none; outline: 0; padding: 0; }
QScrollArea#briefDetail, QScrollArea#actionDetail, QScrollArea#inboxReaderScroll { background: $panel; border: none; }
QListWidget::item { padding: ${item_pad_y}px ${item_pad_x}px; border: none; border-radius: ${radius}px; }
QListWidget::item:selected, QListWidget::item:selected:!active { background: $selection; color: $text; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: transparent; color: $text_secondary; padding: 5px 12px; margin: 2px 2px; border: none; border-radius: ${tab_radius}px; }
QTabBar::tab:hover { color: $text; }
QTabBar::tab:selected { color: $text; background: $selection; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px 1px; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 1px 2px; }
QScrollBar::handle { background: $border_strong; border-radius: 3px; min-height: 24px; min-width: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QWidget#sidebar, QListView#sidebarNav { background: $sidebar; }
QWidget#headerBar { background: $panel; }
QWidget#actionsListPane, QWidget#draftsListPane, QWidget#historyListPane, QWidget#cachedListPane { background: $surface_alt; }
QLabel#headerRefreshIcon { color: $success_fg; }
QWidget#headerTitleLine { background: transparent; }
QDialog { background: $panel; }
QDialog[presentation="drawer"] { background: $panel; border-left: 1px solid $hairline; }
QDialog[presentation="center"] { background: $panel; border: 1px solid $border_strong; border-radius: 12px; }
QWidget#summaryCard { background: $summary; border-left: 2px solid $accent_fg; }
QLabel#summaryHeading { color: $accent_fg; }
QLabel#actionState { color: $primary; }
QLabel#actionDeadline { background: $warning_bg; color: $warning_fg; border-radius: 6px; padding: 6px 9px; }
QLabel#suggestionIcon { color: $accent_fg; background: $accent_bg; border-radius: 8px; }
QLabel#accountAvatar { background: $nav_selection; color: $text_secondary; border-radius: 16px; }
QFrame#accountMenu { background: $panel; border: 1px solid $hairline; border-radius: 10px; }
QPlainTextEdit#draftMessage[inline="true"] { border: none; padding: 16px 0; font-size: 11px; }
QLabel#senderAvatar { background: $canvas; color: $text_secondary; border-radius: 17px; }
QListWidget#cachedMailList { border: none; background: $panel; }
QListWidget#cachedMailList::item { padding: 12px 14px; border-radius: 7px; border: 1px solid transparent; }
QListWidget#cachedMailList::item:selected { background: $selection; color: $text; border: 1px solid $accent_border; }
QPlainTextEdit#cachedMessageDetails { border: none; background: $summary; padding: 14px; }
QPlainTextEdit#savedPreviewText { border: none; background: $panel; padding: 0; font-size: 10px; }
QWidget#savedMailHeader { border-bottom: 1px solid $hairline; }
QLabel#savedMailIcon { background: $accent_bg; border-radius: 7px; }
QWidget#savedMailFooter { border-top: 1px solid $hairline; background: $surface_alt; }
QPlainTextEdit#draftPreviewBody { border: none; background: $panel; padding: 12px 0; }
QPlainTextEdit#draftPreviewBody, QPlainTextEdit#cachedMessageDetails { font-size: 11px; }
QDialog QLabel#dialogHeading { font-size: 21px; }
QDialog#actionEditor QLabel#dialogHeading { font-size: 23px; }
QDialog#actionEditor QLabel { font-size: 10px; }
QDialog#actionEditor QLabel#taskEditorKicker { font-size: 9px; }
QWidget#inlineReplyCard { border: 1px solid $hairline; border-radius: 10px; }
QLabel#draftEditorHeading { font-size: 21px; }
QDialog#draftEditorDialog[inline="true"] QLabel#draftEditorHeading { font-size: 9px; color: $text_muted; }
QDialog#draftEditorDialog[reply="true"] QWidget#draftEditorHeader, QDialog#draftEditorDialog[reply="true"] QWidget#draftEditorFooter { border: none; }
QPlainTextEdit#draftMessage[inline="true"][reply="true"] { border: 1px solid $hairline; background: $surface_alt; border-radius: 7px; padding: 12px; font-size: 10px; }
QPlainTextEdit#draftMessage[inline="true"][reply="true"]:focus { border: 1px solid $accent_border; }
QDialog QLabel#dialogDescription { font-size: 10px; }
QWidget#draftEditorHeader, QWidget#draftsToolbar, QWidget#headerBar { border-bottom: 1px solid $hairline; }
QWidget#draftEditorFooter { border-top: 1px solid $hairline; }
QWidget#actionsToolbar, QWidget#waitingToolbar { border-bottom: 1px solid $hairline; }
QWidget#waitingFollowUp { background: $canvas; border-radius: 8px; }
QDialog#draftEditorDialog { border: none; }
QWidget[inline="true"] QLineEdit { font-size: 10px; border: none; border-bottom: 1px solid $hairline; border-radius: 0; }
QTabBar#calendarViews { background: $canvas; border-radius: 7px; font-size: 9px; }
QTabBar#calendarViews::tab { padding: 6px 12px; font-size: 9px; }
QTabBar#calendarViews::tab:selected { background: $panel; border: none; border-radius: 5px; }
QLabel[metricIcon="true"] { background: $canvas; border-radius: 7px; }
QLabel[metricIcon="true"][attention="true"] { background: $warning_bg; }
QWidget#calendarToolbar { border-bottom: 1px solid $hairline; background: $surface_alt; }
QLabel#brandLabel { color: $text; }
QLabel#brandIcon { background: $text; border-radius: 7px; }
QLabel#pageHeaderTitle, QLabel#detailTitle, QLabel#actionTitle, QLabel#dialogHeading, QLabel#draftPreviewTitle { font-weight: 600; }
QLabel#historyPreviewMeta { color: $success_fg; font-size: 10px; }
QListView#briefList, QListView#actionList { background: $panel; border: none; outline: 0; }
QListView#sidebarNav { border: none; outline: 0; }
QScrollArea#briefDetail, QScrollArea#actionDetail { background: $panel; border: none; }
QListWidget::item { padding: 4px 8px; border-left: 2px solid transparent; }
QListWidget::item:selected, QListWidget::item:selected:!active { background: $selection; color: $text; border-left: 2px solid $accent_border; }
QTabBar::tab { background: transparent; color: $text_secondary; padding: 6px 12px; border: none; }
QTabBar::tab:selected { color: $text; border-bottom: 2px solid $accent_border; }
QTabBar#actionsTabs { background: $canvas; border-radius: 7px; font-size: 9px; }
QTabBar#actionsTabs::tab { font-size: 9px; padding: 7px 10px; border-radius: 5px; }
QTabBar#actionsTabs::tab:selected { background: $panel; border: none; }
QTabBar#settingsTabs { background: $canvas; border-radius: 7px; }
QTabBar#settingsTabs::tab { padding: 7px 12px; border-radius: 5px; }
QTabBar#settingsTabs::tab:selected { background: $panel; border: none; }
"""  # noqa: E501

_current = DARK
_scheme = DEFAULT_SCHEME
_mode = ThemeMode.DARK
_cache_hooked = False


def current_tokens() -> Tokens:
    """The tokens of the applied theme; widgets read them at paint time."""
    return _current


def set_current_tokens(tokens: Tokens) -> None:
    global _current
    _current = tokens


@contextlib.contextmanager
def paused_collection() -> Iterator[None]:
    """No automatic garbage collection inside the block.

    Qt copies its list of every widget, then visits each one, sending events that run
    Python code (``follow_theme``). A collection there can free a window Python no longer
    references, and Qt then visits its freed widgets and crashes. Wrap any work that makes
    Qt walk every widget: setting the style, palette or stylesheet, or repainting all."""
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if enabled:
            gc.enable()


def current_scheme() -> ColorScheme:
    """The colour scheme of the applied theme."""
    return _scheme


def current_mode() -> ThemeMode:
    """Whether the applied theme is the scheme's light or dark set."""
    return _mode


def system_mode(application: QGuiApplication) -> ThemeMode:
    """The system's light or dark mode; light when it doesn't say, since the default
    scheme is a light design."""
    dark = application.styleHints().colorScheme() == Qt.ColorScheme.Dark
    return ThemeMode.DARK if dark else ThemeMode.LIGHT


def current_glass() -> GlassTokens:
    """The glass tokens that go with the applied tokens."""
    return glass_for(current_mode(), _scheme)


class _ThemeFollower[W: QWidget](QObject):
    def __init__(self, widget: W, apply: Callable[[W], None]) -> None:
        super().__init__(widget)
        self._apply = apply

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        # A hidden widget gets only StyleChange: Qt resolves its palette once it shows.
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            self._apply(cast(W, watched))
        return False


def follow_theme[W: QWidget](widget: W, apply: Callable[[W], None]) -> None:
    """Run ``apply(widget)`` now and again whenever a new theme changes ``widget``'s
    palette, for colours a widget keeps rather than reads at paint time, such as a tinted
    icon.

    ``apply`` is handed the widget so it needn't hold one: the follower is the widget's
    child, so an ``apply`` that refers to the widget or a widget above it (a lambda over
    ``self``, a bound method) would make a reference cycle. A dropped window in a cycle is
    left to Python's cycle collector, whose clearing hands each child back to Python and
    deletes it on its own while its parent is half gone: it can crash. Pass a function of
    the widget, such as ``lambda workspace: workspace._request_backdrop()``."""
    apply(widget)
    widget.installEventFilter(_ThemeFollower(widget, apply))


def build_palette(t: Tokens) -> QPalette:
    palette = QPalette()
    role = QPalette.ColorRole
    # Pairs, not a dict keyed by colour: several tokens share a value.
    pairs: tuple[tuple[str, tuple[QPalette.ColorRole, ...]], ...] = (
        (t.panel, (role.Window, role.Base, role.Button)),
        (t.text, (role.WindowText, role.Text, role.ButtonText, role.BrightText)),
        (t.selection, (role.AlternateBase, role.Midlight)),
        (t.canvas, (role.ToolTipBase,)),
        (t.text, (role.ToolTipText,)),
        (t.text_muted, (role.PlaceholderText,)),
        (t.accent_border, (role.Highlight,)),
        (t.text, (role.HighlightedText,)),
        (t.accent_fg, (role.Accent, role.Link, role.LinkVisited)),
        (t.border_strong, (role.Light,)),
        (t.hairline, (role.Mid,)),
        (t.canvas, (role.Dark, role.Shadow)),
    )
    for color, targets in pairs:
        for target in targets:
            palette.setColor(target, QColor(color))
    for target in (role.WindowText, role.Text, role.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, target, QColor(t.text_muted))
    return palette


def build_stylesheet(t: Tokens) -> str:
    metrics = {
        "item_pad_x": ITEM_PAD_X,
        "item_pad_y": ITEM_PAD_Y,
        "radius": RADIUS,
        "pill_radius": PILL_RADIUS,
        "tab_radius": TAB_RADIUS,
        "field_radius": FIELD_RADIUS,
        "menu_item_radius": MENU_ITEM_RADIUS,
    }
    roles = {
        name: getattr(t, name)
        for name in (
            "sidebar",
            "nav_selection",
            "primary",
            "primary_text",
            "summary",
            "surface_alt",
            "success_fg",
            "overlay",
        )
    }
    return string.Template(QSS).substitute(dataclasses.asdict(t) | roles | metrics)


class ThemeStyle(QProxyStyle):
    """Fusion without mnemonic underlines: Alt and the letter still press a button, but no
    letter is underlined, as on macOS. Combo boxes, date fields and spin boxes are drawn
    as rounded fields, like the stylesheet's text fields, with thin chevrons in the
    secondary text colour (the stylesheet can't draw an arrow without an image file).
    Check boxes and radio buttons are drawn as in ``indicators``. Every push button is a
    pill of glass, as Disconnect in Settings is (`paint_button_pill`), whoever made it:
    the app's own, plain ones and Qt's own in message boxes and button rows; its label is
    Medium, and its size matches a `GlassButton`'s."""

    def drawPrimitive(
        self,
        element: QStyle.PrimitiveElement,
        option: QStyleOption,
        painter: QPainter,
        widget: QWidget | None = None,
    ) -> None:
        rect = QRectF(option.rect)
        if _is_pill(widget):
            if element is QStyle.PrimitiveElement.PE_PanelButtonCommand:
                paint_button_pill(painter, option, widget)
                return
            if element is QStyle.PrimitiveElement.PE_FrameDefaultButton:
                return  # The pill is the button; a default one needs no second frame.
            if element is QStyle.PrimitiveElement.PE_FrameFocusRect:
                paint_button_focus(painter, option, widget)
                return
        if (
            element is QStyle.PrimitiveElement.PE_FrameFocusRect
            and isinstance(widget, QCheckBox)
            and not widget.text()
        ):
            paint_box_focus(painter, option)
            return
        if element in _CHEVRONS:
            # A menu button's arrow on the primary pill is white, as its label is.
            on_fill = _is_pill(widget) and _is_primary(widget)
            color = current_glass().primary_text if on_fill else _arrow_color(option)
            paint_chevron(painter, rect, color, up=element in _UP)
            return
        state = option.state
        on = bool(state & QStyle.StateFlag.State_On)
        enabled = bool(state & QStyle.StateFlag.State_Enabled)
        if element in _CHECK_BOXES:
            partial = bool(state & QStyle.StateFlag.State_NoChange)
            paint_check_box(
                painter, rect, current_tokens(), checked=on, partial=partial, enabled=enabled
            )
            return
        if element is QStyle.PrimitiveElement.PE_IndicatorRadioButton:
            paint_radio(painter, rect, current_tokens(), checked=on, enabled=enabled)
            return
        super().drawPrimitive(element, option, painter, widget)

    def drawControl(
        self,
        element: QStyle.ControlElement,
        option: QStyleOption,
        painter: QPainter,
        widget: QWidget | None = None,
    ) -> None:
        if (
            element is QStyle.ControlElement.CE_PushButtonLabel
            and _is_pill(widget)
            and _is_primary(widget)
            and isinstance(option, QStyleOptionButton)
        ):
            # The primary pill's label is white on its solid blue.
            label = QStyleOptionButton(option)
            label.palette.setColor(
                QPalette.ColorRole.ButtonText, QColor(current_glass().primary_text)
            )
            super().drawControl(element, label, painter, widget)
            return
        super().drawControl(element, option, painter, widget)

    def drawComplexControl(
        self,
        control: QStyle.ComplexControl,
        option: QStyleOptionComplex,
        painter: QPainter,
        widget: QWidget | None = None,
    ) -> None:
        if isinstance(option, QStyleOptionComboBox) and control is _COMBO:
            paint_field(painter, QRectF(option.rect), option.state)
            arrow = self._part(control, option, _COMBO_ARROW, widget)
            paint_chevron(painter, QRectF(arrow), _arrow_color(option))
            return
        if isinstance(option, QStyleOptionSpinBox) and control is _SPIN:
            paint_field(painter, QRectF(option.rect), option.state)
            if option.buttonSymbols is not QAbstractSpinBox.ButtonSymbols.NoButtons:
                for part, up in ((_SPIN_UP, True), (_SPIN_DOWN, False)):
                    rect = self._part(control, option, part, widget)
                    paint_chevron(painter, QRectF(rect), _arrow_color(option), up=up)
            return
        super().drawComplexControl(control, option, painter, widget)

    def subControlRect(
        self,
        control: QStyle.ComplexControl,
        option: QStyleOptionComplex,
        part: QStyle.SubControl,
        widget: QWidget | None = None,
    ) -> QRect:
        rect = super().subControlRect(control, option, part, widget)  # type: ignore[arg-type]
        if (control, part) in _EDIT_FIELDS:
            # Text starts where it does in a text field: 8 px padding inside the border.
            rect.setLeft(option.rect.left() + 1 + _FIELD_PAD_X)
        return rect

    def sizeFromContents(
        self,
        contents: QStyle.ContentsType,
        option: QStyleOption,
        size: QSize,
        widget: QWidget | None = None,
    ) -> QSize:
        if contents is QStyle.ContentsType.CT_PushButton and _is_pill(widget):
            # As a GlassButton: the label (and icon) plus padding and room round the pill.
            return QSize(
                size.width() + 2 * (_PILL_PAD_X + _PILL_ROOM_X),
                size.height() + 2 * (_PILL_PAD_Y + _PILL_ROOM_Y),
            )
        result = super().sizeFromContents(contents, option, size, widget)  # type: ignore[arg-type]
        if contents in _FIELDS:
            line = option.fontMetrics.height()
            result.setHeight(max(result.height(), line + _FIELD_EXTRA_Y))
        return result

    def _part(
        self,
        control: QStyle.ComplexControl,
        option: QStyleOptionComplex,
        part: QStyle.SubControl,
        widget: QWidget | None,
    ) -> QRect:
        return self.subControlRect(control, option, part, widget)

    def polish(
        self, target: QApplication | QWidget | QPalette | Qt.GlobalColor | QColor, /
    ) -> None:
        """Pop-ups (menus, combo box lists and tooltips) are windows of their own: a
        translucent window lets their rounded corners show, rather than square corners of
        the window's colour. The stylesheet rounds menus and tooltips; its rules never
        reach a combo box's private list window, so `_RoundedPopup` paints that one. A
        combo box's own item delegate ignores the stylesheet's rounded item rules, so it
        gets a styled one."""
        if isinstance(target, QWidget) and any(target.inherits(name) for name in _POPUPS):
            target.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            if target.inherits(_COMBO_LIST):
                target.installEventFilter(_rounded_popup())
        if isinstance(target, QPushButton) and _is_pill(target) and not target.font().bold():
            font = target.font()  # Keeps a size set before; a Medium label, as Disconnect's.
            font.setWeight(QFont.Weight.Medium)
            target.setFont(font)
        if isinstance(target, QDialog) and target.property("presentation") not in (
            "drawer",
            "center",
        ):
            # The home screen's style: glass round a rounded sheet (ui/glass imports this
            # package, so it is imported here).
            from mailbrief.ui.glass.dialog import dialog_sheet

            target.installEventFilter(dialog_sheet())
        if isinstance(target, QComboBox):
            delegate = target.itemDelegate()
            if delegate is None or delegate.metaObject().className() in _COMBO_DELEGATES:
                target.setItemDelegate(QStyledItemDelegate(target))
        super().polish(target)

    def pixelMetric(
        self,
        metric: QStyle.PixelMetric,
        option: QStyleOption | None = None,
        widget: QWidget | None = None,
    ) -> int:
        if metric in _DIALOG_MARGINS and isinstance(widget, QDialog):
            return DIALOG_MARGIN  # A dialog's content sits inside its sheet.
        return super().pixelMetric(metric, option, widget)

    def styleHint(
        self,
        hint: QStyle.StyleHint,
        option: QStyleOption | None = None,
        widget: QWidget | None = None,
        returnData: QStyleHintReturn | None = None,
    ) -> int:
        if hint == QStyle.StyleHint.SH_UnderlineShortcut:
            return 0
        return super().styleHint(hint, option, widget, returnData)


# A push button drawn as a pill: its padding and the room kept round it (logical px), the
# same as a GlassButton's (ui/glass/button.py), and how a pressed one shrinks.
_PILL_PAD_X: Final = 10
_PILL_PAD_Y: Final = 6
_PILL_ROOM_X: Final = 3
_PILL_ROOM_Y: Final = 3
_PILL_PRESSED: Final = 0.97
_PILL_DISABLED: Final = 0.5
_PILL_FOCUS_WIDTH: Final = 2.0
# The margins of a dialog's own layout, which keep its content inside its sheet.
_DIALOG_MARGINS: Final = frozenset(
    {
        QStyle.PixelMetric.PM_LayoutLeftMargin,
        QStyle.PixelMetric.PM_LayoutTopMargin,
        QStyle.PixelMetric.PM_LayoutRightMargin,
        QStyle.PixelMetric.PM_LayoutBottomMargin,
    }
)
# Buttons that paint themselves: the glass pill and the sidebar's rows.
_SELF_PAINTED: Final = frozenset({"glass", "nav"})


def _is_pill(widget: QWidget | None) -> bool:
    """A push button the style draws as a pill (not one that paints itself)."""
    return isinstance(widget, QPushButton) and widget.property("variant") not in _SELF_PAINTED


def _is_primary(widget: QWidget | None) -> bool:
    return widget is not None and widget.property("variant") == "primary"


def _pill_rect(option: QStyleOption) -> QRectF:
    rect = QRectF(option.rect).adjusted(_PILL_ROOM_X, _PILL_ROOM_Y, -_PILL_ROOM_X, -_PILL_ROOM_Y)
    if option.state & (QStyle.StateFlag.State_Sunken | QStyle.StateFlag.State_On):
        centre = rect.center()
        rect.setSize(rect.size() * _PILL_PRESSED)
        rect.moveCenter(centre)
    return rect


def paint_button_pill(painter: QPainter, option: QStyleOption, widget: QWidget | None) -> None:
    """A push button as a pill of glass, as Disconnect in Settings: brighter under the
    pointer, a little smaller while pressed, at half strength while disabled. Glass is
    imported here: ui/glass imports this package."""
    from mailbrief.ui.glass.painter import paint_liquid_pill, paint_pill
    from mailbrief.ui.glass.preferences import glass_preferences

    state = option.state
    enabled = bool(state & QStyle.StateFlag.State_Enabled)
    hover = 1.0 if enabled and state & QStyle.StateFlag.State_MouseOver else 0.0
    glass, mode = current_glass(), glass_preferences().mode
    primary = _is_primary(widget)
    painter.save()
    if not enabled:
        painter.setOpacity(_PILL_DISABLED)
    if glass.highlight.alpha > 0:
        paint_liquid_pill(
            painter, _pill_rect(option), glass, primary=primary, mode=mode, hover=hover
        )
    else:
        paint_pill(painter, _pill_rect(option), glass, primary=primary, mode=mode, hover=hover)
    painter.restore()


def paint_button_focus(painter: QPainter, option: QStyleOption, widget: QWidget | None) -> None:
    """A 2 px ring inside the pill, only after the keyboard moved focus, as a GlassButton's:
    the accent, or the label's colour on the primary's solid fill."""
    state = option.state
    if not (
        state & QStyle.StateFlag.State_HasFocus
        and state & QStyle.StateFlag.State_KeyboardFocusChange
    ):
        return
    rect = QRectF(option.rect)
    if isinstance(widget, QPushButton):
        rect = QRectF(widget.rect()).adjusted(
            _PILL_ROOM_X, _PILL_ROOM_Y, -_PILL_ROOM_X, -_PILL_ROOM_Y
        )
    inset = _PILL_FOCUS_WIDTH / 2
    ring = rect.adjusted(inset, inset, -inset, -inset)
    color = current_glass().primary_text if _is_primary(widget) else current_tokens().accent_fg
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(color), _PILL_FOCUS_WIDTH))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(ring, ring.height() / 2, ring.height() / 2)
    painter.restore()


def paint_box_focus(painter: QPainter, option: QStyleOption) -> None:
    """A check box without text, as a step's (B04), has no label for the focus to ring: a
    2 px ring on the box itself, only after the keyboard moved focus, in the accent, or the
    mark's colour on a ticked box's fill."""
    state = option.state
    if not (
        state & QStyle.StateFlag.State_HasFocus
        and state & QStyle.StateFlag.State_KeyboardFocusChange
    ):
        return
    tokens = current_tokens()
    on = bool(state & (QStyle.StateFlag.State_On | QStyle.StateFlag.State_NoChange))
    color = indicator_colors(on, tokens)[2] if on else tokens.accent_fg
    inset = _PILL_FOCUS_WIDTH / 2
    ring = QRectF(option.rect).adjusted(inset, inset, -inset, -inset)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(color), _PILL_FOCUS_WIDTH))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(ring, BOX_RADIUS, BOX_RADIUS)
    painter.restore()


# Windows of their own, rounded: menus, combo box lists and tooltips.
_COMBO_LIST: Final = "QComboBoxPrivateContainer"
_POPUPS: Final = ("QMenu", _COMBO_LIST, "QTipLabel")
# The delegates a combo box makes for itself, which ignore the stylesheet's item rules.
_COMBO_DELEGATES: Final = frozenset({"QComboBoxDelegate", "QComboMenuDelegate"})


class _RoundedPopup(QObject):
    """Paints a combo box's list window as a rounded panel with a hairline edge, in the
    colours of the theme in effect, in place of its square frame. Its list draws over
    it."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.Paint or not isinstance(watched, QWidget):
            return False
        tokens = current_tokens()
        painter = QPainter(watched)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(tokens.hairline), 0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(QColor(tokens.panel))
        half = 0.5 / watched.devicePixelRatioF()
        box = QRectF(watched.rect()).adjusted(half, half, -half, -half)
        painter.drawRoundedRect(box, RADIUS, RADIUS)
        painter.end()
        return True  # Its own paint would fill the square.


_popup_painter: _RoundedPopup | None = None


def _rounded_popup() -> _RoundedPopup:
    global _popup_painter
    if _popup_painter is None:
        _popup_painter = _RoundedPopup()
    return _popup_painter


_UP: Final = frozenset({QStyle.PrimitiveElement.PE_IndicatorArrowUp})
_CHEVRONS: Final = _UP | {QStyle.PrimitiveElement.PE_IndicatorArrowDown}
_CHECK_BOXES: Final = frozenset(
    {
        QStyle.PrimitiveElement.PE_IndicatorCheckBox,
        QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck,
    }
)
_COMBO: Final = QStyle.ComplexControl.CC_ComboBox
_COMBO_ARROW: Final = QStyle.SubControl.SC_ComboBoxArrow
_SPIN: Final = QStyle.ComplexControl.CC_SpinBox
_SPIN_UP: Final = QStyle.SubControl.SC_SpinBoxUp
_SPIN_DOWN: Final = QStyle.SubControl.SC_SpinBoxDown
# Pairs: a sub-control's value means different parts in different controls (a combo box's
# edit field is a spin box's down button).
_EDIT_FIELDS: Final = frozenset(
    {
        (QStyle.ComplexControl.CC_ComboBox, QStyle.SubControl.SC_ComboBoxEditField),
        (QStyle.ComplexControl.CC_SpinBox, QStyle.SubControl.SC_SpinBoxEditField),
    }
)
_FIELDS: Final = frozenset({QStyle.ContentsType.CT_ComboBox, QStyle.ContentsType.CT_SpinBox})
# Text fields' stylesheet padding is 4 px 8 px; drawn fields match it. A text field is
# its font's line height plus 12 px (that padding, a 1 px border and Qt's 1 px margin,
# above and below); drawn fields are never shorter, on any platform (Fusion's own spin box
# height differs between them).
_FIELD_PAD_X: Final = 8
_FIELD_EXTRA_Y: Final = 12
CHEVRON_PX: Final = 8.0  # Width; the height is half of it.


def _arrow_color(option: QStyleOption) -> str:
    tokens = current_tokens()
    enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
    return tokens.text_secondary if enabled else tokens.text_muted


def paint_field(painter: QPainter, rect: QRectF, state: QStyle.StateFlag) -> None:
    """A field's rounded box: the panel colour inside a 1 px hairline, which turns the
    accent colour while the field has focus, as the stylesheet draws text fields."""
    tokens = current_tokens()
    focused = bool(state & QStyle.StateFlag.State_HasFocus)
    pen = QPen(QColor(tokens.accent_fg if focused else tokens.hairline), 1.0)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(pen)
    painter.setBrush(QColor(tokens.panel))
    box = rect.adjusted(0.5, 0.5, -0.5, -0.5)
    painter.drawRoundedRect(box, FIELD_RADIUS, FIELD_RADIUS)
    painter.restore()


def paint_chevron(painter: QPainter, rect: QRectF, color: str, *, up: bool = False) -> None:
    """A thin chevron centred in ``rect``, pointing down (or up)."""
    width = min(CHEVRON_PX, rect.width(), rect.height() * 2)
    half = width / 2
    centre = rect.center()
    rise = -half / 2 if up else half / 2
    pen = QPen(QColor(color), 1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPolyline(
        [
            QPointF(centre.x() - half, centre.y() - rise),
            QPointF(centre.x(), centre.y() + rise),
            QPointF(centre.x() + half, centre.y() - rise),
        ]
    )
    painter.restore()


# The application owns its style; this reference keeps the Python object, and so its
# override, alive for as long as it is the application's style.
_style: ThemeStyle | None = None


def palette_tokens(application: QApplication) -> Tokens:
    """The tokens that suit the palette in effect: light on a light window colour."""
    return tokens_for(_palette_mode(application), _scheme)


def _palette_mode(application: QApplication) -> ThemeMode:
    window = application.palette().color(QPalette.ColorRole.Window)
    return ThemeMode.LIGHT if window.lightness() > 127 else ThemeMode.DARK


def _follow_palette(application: QApplication) -> None:
    """Tokens and mode that suit the palette in effect, after a failed theme."""
    global _mode
    _mode = _palette_mode(application)
    set_current_tokens(tokens_for(_mode, _scheme))


def apply_theme(
    app: QApplication,
    mode: ThemeMode = ThemeMode.DARK,
    scheme: ColorScheme = DEFAULT_SCHEME,
) -> Tokens:
    """Apply Fusion (as ThemeStyle), the bundled font, the palette and the stylesheet for
    ``mode`` in ``scheme``, all or nothing.

    Without the bundled font the app keeps Qt's font; the colours still apply. If a step
    fails, the stylesheet, style, palette and font in effect before are put back, the
    tokens follow the restored palette, and the error is raised for the caller to log.
    """
    global _style, _cache_hooked, _scheme, _mode
    stylesheet, palette, font = app.styleSheet(), app.palette(), app.font()
    replaced: str | None = None  # The style's name, when this call replaces it.
    with paused_collection():
        try:
            tokens = tokens_for(mode, scheme)
            # A stylesheet wraps the style in a proxy that hides its name; clear it first.
            app.setStyleSheet("")
            if not isinstance(app.style(), ThemeStyle):
                current = app.style()
                base = current.baseStyle() if isinstance(current, QProxyStyle) else current
                replaced = base.name()
                _style = ThemeStyle(QStyleFactory.create("Fusion"))
                app.setStyle(_style)
            if register_fonts():
                app.setFont(ui_font(TEXT_PX))
            # Before the palette: widgets following the theme read the tokens as it changes.
            set_current_tokens(tokens)
            previous, _scheme = _scheme, scheme
            previous_mode, _mode = _mode, mode
            try:
                app.setPalette(build_palette(tokens))
                app.setStyleSheet(build_stylesheet(tokens))
            except BaseException:
                _scheme, _mode = previous, previous_mode
                raise
        except BaseException:
            _restore(app, stylesheet, palette, font, replaced)
            raise
    if not _cache_hooked:
        # Release the cached icon pixmaps, fonts and metrics while Qt can still free them.
        app.aboutToQuit.connect(release_icon_cache)
        app.aboutToQuit.connect(release_font_cache)
        _cache_hooked = True
    return tokens


def _restore(
    app: QApplication, stylesheet: str, palette: QPalette, font: QFont, replaced: str | None
) -> None:
    """Put back what apply_theme found. Each step runs even if one before it fails, and no
    failure here hides the error that made apply_theme fail."""
    global _style
    with contextlib.suppress(Exception):
        app.setStyleSheet("")  # So app.style() below is the style itself, not a proxy.
    if replaced is not None:
        with contextlib.suppress(Exception):
            app.setStyle(replaced)
    with contextlib.suppress(Exception):
        if not isinstance(app.style(), ThemeStyle):
            _style = None  # ThemeStyle is no longer the app's style.
    steps: tuple[Callable[[], object], ...] = (
        lambda: app.setPalette(palette),
        lambda: app.setFont(font),
        lambda: app.setStyleSheet(stylesheet),
        lambda: _follow_palette(app),
    )
    for step in steps:
        with contextlib.suppress(Exception):
            step()
