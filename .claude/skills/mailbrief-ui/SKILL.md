---
name: mailbrief-ui
description: Use for any change to Forewit's desktop UI under src/mailbrief/ui, including layout, styling, widgets, icons, fonts, the theme and the three-pane workspace. Gives the design tokens, Qt styling rules, plain-text safety rules and the screenshot review loop.
---

# Forewit desktop UI

Read AGENTS.md first; its invariants win. Component specs and the token table are in
`references/spec.md`. The default look is Studio (`docs/ui/studio.md`: white and Studio
Mist grey, one blue, Semibold titles, flat buttons, filled cards with no borders or
shadows), in the system's light or dark mode. Its approved renders are
`docs/ui/studio-light.png` and `docs/ui/studio-dark.png` (the window at 1100 × 720) and
`docs/ui/studio-dialog.png` (a dialog). The layout's original mockup is
`docs/ui/mockup-three-pane-dark.png`. Pages ported to the Today template use its compact
list, body and meta sizes (`docs/ui-redesign.md`).

## Rules
- Colours come only from `ui/theme/tokens.py`. Widgets read `current_tokens()` at paint
  time, or keep a colour (a tinted icon) only through `follow_theme()`, so it changes with
  the owner's colour scheme; never hard-code a hex colour anywhere else under
  `src/mailbrief/ui`. Every scheme passes the contrast tests in both modes.
- Open a window's dialogs with `MainWindow.dialog_guard.show_dialog(dialog)`, never
  `QDialog.open()`, `exec()` or `setWindowModality()`. On macOS a modal dialog runs an
  AppKit modal session, which drops Cmd+Q and the Dock's Quit, so Forewit could only be
  force quit. A file picker or message box opened from a dialog goes through that
  dialog's own `dialog_guard`. A dialog holding typed input implements
  `has_unsaved_input()` and is listed in `MainWindow._unsaved_input()`, so quitting asks
  before losing it.
- Mail and AI text reaches the screen only through `plain_label()` (`ui/labels.py`, plain
  text, word wrap), `wrap_label()` (plain text that also wraps inside a long unbroken
  token) or a delegate's or a painted widget's `drawText()` (the Inbox's monogram).
  Never put it in a tooltip, and never use rich text
  or automatic links for it. Use `wrap_label()` wherever an address, link or long word
  could otherwise widen the layout.
- Every button is flat, as the fields and the Today template's switchers are: an 8 px
  (`FIELD_RADIUS`) rounded rectangle, `panel` in a `hairline` edge, `card` under the
  pointer, darker pressed, `selection` in an `accent_border` edge when checked, no
  animation, and a focus ring only after the keyboard moved focus. `ThemeStyle` draws
  every `QPushButton` (`paint_button`, `button_colors`): `outline_button()`'s and Qt's own
  in message boxes and button boxes. Make buttons with `outline_button()` in
  ui/labels.py; `primary=True` fills the one primary action of a screen with
  `primary_fill`, and `compact=True` makes a dense row's label `COMPACT_PX` (9); every
  other label is `TEXT_PX` Medium, so never set a button's font by hand. Never style
  `QPushButton` in the stylesheet; only the sidebar's `variant="nav"` rows are. Button
  text is escaped with `button_label()` unless it is app text carrying its own `&`
  mnemonic (`mnemonic=True`); long mail-derived text uses `shorten=True`.
- Make every date field's drop-down calendar with `calendar_popup(field)`
  (`ui/date_picker.py`): a flat `DatePicker`, never Qt's own (the source rules refuse
  `setCalendarPopup` elsewhere).
- Text is the bundled DM Sans (`FONT_FAMILY`), through `ui_font(px, medium=...,
  semibold=...)`; `tabular=True` gives Inter's equal-width figures for times and counts
  in columns. Medium is for emphasis in text, Semibold for titles; sizes are in pixels,
  never points, and display sizes are tracked tighter automatically. Metrics: `TEXT_PX`
  13, `READING_PX` 14, `SMALL_PX` 12, `CAPTION_PX` 11, `HEADING_PX` 17, `TITLE_PX` 20,
  `RADIUS` 10, `CARD_RADIUS` 18, `PILL_RADIUS` 14, `FIELD_RADIUS` 8, `SIDEBAR_WIDTH` 214.
- A screen has at most one primary button (`primary_fill`). Status text
  (deadlines, proposals) is bare coloured text, never a filled chip. Cards are
  `HairlineFrame`s: a `card` fill, no border. Combo boxes, date, time and number fields,
  check boxes and radio buttons are drawn by `ThemeStyle`; don't style them in QSS.
- Icons are plain: a button's icon (`icon_name`) is drawn in the label's colour beside
  the label, never in a tile or square behind it. An icon with no label makes a square
  button; give it an accessible name and a tooltip.
- Glass panels are the chrome only: the header, sidebar, gutter, status strip and a
  dialog's frame, never a button. Pages and a dialog's content sit on a white sheet, and the selected
  page is a fill, never a second layer of glass. Studio, the default scheme, keeps the
  chrome flat: its glass backdrop is flat and its lights are off (`GlassTokens`).
- Dialogs get the home screen's style automatically: `ThemeStyle.polish` installs
  `DialogSheet` (`ui/glass/dialog.py`) on every `QDialog`, which paints the glass with its
  colour field round a rounded sheet (`DIALOG_GUTTER` 8 from the edge, `SHEET_RADIUS`
  12), and gives the dialog's own layout `DIALOG_MARGIN` (22) so its content sits inside
  the sheet. Keep a dialog's content in its own top-level layout with those default
  margins; never paint a dialog's background, give it a stylesheet background or set its
  top-level margins. The exception is a dialog with the `presentation` property
  `"center"` (Saved mail) or `"drawer"` (the Calendar's event editor, Google event,
  Plan with AI and block editor): `dialog_guard` shows it inside the window, in the Today
  template's own layout instead of a `DialogSheet`.
- Nothing has a sharp corner. Pages sit on the workspace's rounded sheet; never add a
  divider that meets the chrome or another hairline. Anything filled or outlined is
  rounded (`RADIUS`, `CARD_RADIUS`, pills), and pop-ups are rounded by `ThemeStyle`.
- Measure `ui_font()` text with `ui_metrics(px, medium=...)`, built once per size. `ui_font()`
  returns a copy of a cached font; both caches are cleared when the fonts are registered
  and when the app quits.
- Icons are bundled Tabler outlines, coloured at render time: `icon(name, color, px)` or
  `icon_pixmap(...)`. Add a new icon only with its SVG and an `ICON_NAMES` entry.
- Hairlines on cards and dividers are painted with cosmetic pens (`ui/hairline.py`:
  `HairlineFrame`, `HairlineDivider`, `HairlineSplitter`), never with QSS borders, so they
  stay one device pixel at every scale.
- Keep MainWindow's attribute names; tests and other modules use them.
- Make list widgets from `SafeList` (ui/lists.py; an `ActivatingList` is one), never
  `QListWidget` itself, whose `clear()` can crash on Cocoa when a garbage collection runs
  inside Qt's deletion; `SafeList.clear()` is `clear_list()`.
- Nothing inside a window may keep a reference back to the window: no attribute holding
  it, and no function or bound method over it kept in an attribute (a clock, a
  callback, a theme follower's `apply`). That makes a cycle, and a dropped window in a
  cycle is left to Python's cycle collector, whose `tp_clear` hands each child back to
  Python and deletes it on its own while its parent is half gone, which can crash. Find
  the window with `parentWidget()`, hold it with `weakref.ref` (`GlassChrome.workspace`)
  or keep its method with `weak_method` (`ui/weak.py`), and give `follow_theme` a
  function of the widget it is handed. Signal connections are fine: Qt holds them, so a
  `connect(lambda: self...)` makes no cycle the collector sees. Checked by
  `test_nothing_in_the_main_window_refers_back_to_it` (`tests/ui/references.py`) and the
  `..._is_freed_without_the_cycle_collector` tests.
- Every new widget gets an `objectName` and an accessible name. Rows a keyboard must skip
  (section headers) have `NoItemFlags`; Return and Enter activate a row once.
- Keep the Tab order in `MainWindow._link_tab_order` (see the spec), and never call
  `nextInFocusChain()` or `previousInFocusChain()` from Python: PySide re-parents the
  returned wrapper under the caller, and deleting the caller later invalidates it.
- No blocking network or database work on the Qt main thread. Add no dependencies.
- Bundled files resolve through `Path(__file__)` and are listed in
  `scripts/build_desktop.py`; `ui/smoke.py` checks they load in the package.

## Tests
- The offscreen platform ignores `setColorScheme`, so tests call `apply_theme`
  explicitly, and every test that calls it (directly or through `app.main`) takes the
  `themed` fixture from `tests/ui/conftest.py`, which restores the style, palette,
  stylesheet, font, tokens and the default `QLocale` (`app.main` sets it to English).
- On macOS, UI tests need `MACOS_GUI_AVAILABLE=1` (or a real session) and run offscreen
  with `QT_QPA_PLATFORM=offscreen`. CI also runs them on the real macOS platform
  (`MACOS_GUI_AVAILABLE=1 QT_QPA_PLATFORM=cocoa uv run pytest tests/ui tests/unit/test_app.py`),
  which catches platform-only sizes and high-DPI fills; run that before pushing a change
  to painting or sizes. Locally a test can fail there with "not activated in 5000 ms"
  when the desktop won't give the window focus; that isn't a bug in the change.
- Keep the 680 px width tests passing (`test_unbroken_mail_text_never_widens_the_workspace`,
  `test_a_long_unbroken_title_never_widens_the_page`): they limit how wide buttons, pill
  padding and margins can grow.
- Never assert a top-level window's requested size: CI's macOS screen is small (about
  1024 × 649 available), and Cocoa keeps windows inside it. Derive expectations from the
  actual geometry (a list shorter than its rows scrolls) or from the screen's available
  area, and skip a check that needs a wider screen than the one present. Get the screen
  with `QGuiApplication.primaryScreen()` or `screenAt()`, never `widget.screen()`, which
  re-parents the shared QScreen wrapper under the widget.
- `tests/unit/test_source_rules.py` runs with every test run: it fails, naming the file and
  line, on a hex colour under `ui` outside `ui/theme/tokens.py`, on a `QListWidget` made or
  subclassed under `ui` outside `ui/lists.py`, and on a strftime directive that follows the
  system language (`%a`, `%A`, `%b`, `%B`, `%p`, `%c`, `%x`) anywhere in `src/mailbrief`.

## Screenshot review loop
1. Render the gallery at 1x and 2x (PowerShell: set `$env:MAILBRIEF_UI_SHOTS` instead; on
   macOS add `MACOS_GUI_AVAILABLE=1 QT_QPA_PLATFORM=offscreen`):
   ```
   MAILBRIEF_UI_SHOTS=scratch/ui-shots uv run pytest tests/ui/test_main_window_layout.py tests/ui/test_workspace.py -q --no-cov
   QT_SCALE_FACTOR=2 MAILBRIEF_UI_SHOTS=scratch/ui-shots uv run pytest tests/ui/test_main_window_layout.py tests/ui/test_workspace.py -q --no-cov
   ```
   The whole window renders as `window-desktop-{mode}-{dpr}x.png`; the workspace as
   `{size}-{mode}-{dpr}x.png`, sizes `mockup` (680×520) and `desktop` (1100×720); the
   pages as `page-*` (Actions as `page-actions-*`, `page-waiting-*` and
   `page-completed-dark-*`); the project dialog as `dialog-project-*`.
2. Compare `window-desktop-light-1x.png` and `window-desktop-dark-1x.png` with
   `docs/ui/studio-light.png` and `docs/ui/studio-dark.png`, and any dialog with
   `docs/ui/studio-dialog.png`; check `mockup-*-2x.png` for the narrow layout. List
   differences in spacing, sizes, weights, colours and alignment; fix them; render again.
   At most three rounds per change. When the owner approves a change to the look, copy
   the new renders over those three references in the same pull request.
3. Look at both modes' renders for anything hard to read.
4. Never commit `scratch/`.
