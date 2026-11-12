# UI file map and rules

Moved here from AGENTS.md so sessions that do not touch the UI don't carry it. Read it
before any change under `src/mailbrief/ui`; AGENTS.md's invariants still win.

## File map

- `src/mailbrief/ui/`: PySide6 workflow,
  desktop service composition, saved-brief display, the Actions page (`actions_view.py`:
  rows and lists; `action_detail.py`: the selected action; `action_text.py`: the details
  both show) and the action editor, the drafts pane (`drafts_view.py`), editor
  (`draft_editor.py`) and its Write with AI panel (`drafting_panel.py`), the Settings
  Preferences tab (`preferences_view.py`), the Appearance tab (`appearance_view.py`; the
  choice is saved in `appearance.json` beside the database by `appearance.py`, never in
  `desktop-settings.json`, which older builds would refuse) and the Briefs page for saved briefs and missed
  days (`history_view.py`), the Proposals dialog for one action and the wording of a
  proposal's effect (`proposals_view.py`), the Inbox page (`inbox_view.py`; its rows by
  day in `inbox_list.py`, its reading pane in `inbox_reader.py`); `lists.py` holds
  `ActivatingList`, where
  Return or Enter activates a row once on every platform, `clear_list`, which empties a
  `QListWidget`, and `SafeList`, the list widget whose `clear()` is `clear_list`; `layouts.py`
  holds `take_widget`, which moves a widget out of its layout so PySide drops the item's
  wrapper;
  `HeaderRowsModel`
  (`brief_list.py`) is the model of a painted list with section headers, which the keyboard
  skips; `dialog_guard.py` shows dialogs
  non-modal behind a cover over the window, so Cmd+Q and the Dock's Quit work with one open.
- `ui/glass/`: Liquid Glass: `backdrop.py` paints the backdrop
  from the glass tokens at a quarter of the window's size and blurs it, off the Qt thread
  (`BackdropRenderer`, which caches four and drops stale renders), and `painter.py` paints a
  glass panel and a raised pill, with opaque fallbacks (`GlassMode`). Glass is the chrome
  only: buttons are flat, drawn by `ThemeStyle` (see `ui/theme/`). `dialog.py` (`DialogSheet`) gives every
  dialog the home screen's glass round a rounded sheet. `motion.py` holds the fluid easing
  the sidebar's selection glides on. The header and sidebar are glass
  (`workspace.GlassChrome`) over the workspace's backdrop, and the selected page is a
  raised pill; muted text in the sidebar shows as secondary there. `system.py` reads the
  system's Increase contrast, Reduce transparency and Reduce motion (Qt, NSWorkspace by
  typed `objc_msgSend`, the Windows registry and `SystemParametersInfo`; a failed read is
  no preference), and `accessibility.py` (`GlassAccessibility`) combines them with the
  owner's Settings > Appearance choices into `glass_preferences()`, again whenever the app
  becomes active or Qt reports a contrast change. `profile.py` times glass paints with
  `MAILBRIEF_PAINT_PROFILE=1` and logs counts and percentiles at quit. What was built, its
  measured cost and what wasn't built are in `docs/ui/liquid-glass.md`.
- `ui/scheduler.py` (`RefreshScheduler`, the minute tick behind timed refresh) and
  `ui/auto_send_view.py` (the automatic-analysis permission dialog); `services/consent.py`
  reads and sets that permission.
- `ui/theme/`: design tokens (`tokens.py`, the only place UI colours are defined: a dark
  and a light set for each `ColorScheme`, Studio by default, and the Liquid Glass tokens),
  the Fusion palette and stylesheet (`style.py`, `apply_theme`; `follow_theme` re-colours
  what a widget keeps, such as a tinted icon, when the scheme changes; `ThemeStyle` draws
  every push button flat (`paint_button`: an 8 px rounded rectangle in `button_colors`, the
  one primary `primary_fill`, a keyboard-only focus ring; icons plain, beside the label),
  fields, chevrons, check boxes and radio buttons, with
  `indicators.py`, and installs `DialogSheet` on every dialog), bundled DM Sans for
  text and Inter Regular, Medium and SemiBold for tabular figures, under the SIL OFL
  (`fonts/`), and Tabler icons under MIT
  (`icons/`). `ui/labels.py` holds
  `plain_label`, `wrap_label` (`WrapLabel`), `button_label` and `outline_button` (every
  button; `compact=True` for dense rows). Every date field's drop-down calendar is
  `ui/date_picker.py`'s flat `DatePicker`, through `calendar_popup()`. MainWindow hosts the three-pane workspace
  (`workspace.py`, with `hairline.py`, `brief_list.py` and `brief_detail.py`): the header
  (when Gmail was last checked, Sync and review, Cancel), the sidebar's pages and footer
  (Saved mail, Data, Settings, and Google sign-in while signed out; the Gmail and AI lines
  and Disconnect are in Settings' Account card), the Today,
  run (review and consent), Actions, Drafts and Briefs pages, and a status strip with
  Undo.

## Rules

- Render the screenshot gallery with
  `MAILBRIEF_UI_SHOTS=scratch/ui-shots uv run pytest tests/ui/test_workspace.py -q --no-cov`
  and compare its layout with `docs/ui/mockup-three-pane-dark.png` (Graphite's colours).
  Every colour scheme must pass the contrast tests in `tests/ui/theme/` in both modes.
- Cards and glass rims snap their 1-device-pixel lines to the pixel grid with
  `hairline.crisp_rect()`, so they stay crisp at 125% and 150%.
- Wrap work that makes Qt walk every widget (setting the style, palette or stylesheet,
  repainting all) in `paused_collection()`: Qt visits a copied list of widgets and runs
  Python code for each, and a garbage collection there could free one it still visits.
- Make list widgets from `lists.SafeList` (an `ActivatingList` is one), never `QListWidget`
  itself: Qt's `clear()` deletes the items inside its model reset, and a garbage collection
  of their Python wrappers or data there can crash on Cocoa (CI's macOS UI step).
  `SafeList.clear()` is `lists.clear_list()`, which takes each item out with collection
  paused and changes the current row once, as Qt's `clear()` does.
- Before another layout (or another cell of the same grid) takes a widget, call
  `layouts.take_widget(widget)`, and never `setParent` a widget out of a layout added to
  another with `addLayout` or `insertLayout` after it was filled. PySide wraps that
  layout's items; when Qt deletes one behind PySide's back, its wrapper stays registered at
  the freed address, and a QObject later made there gets it as its own wrapper (a Windows
  access violation in `SignalManager::retrieveMetaObject`). A layout filled only by
  `addWidget` on itself has no such wrappers. After every UI test, `tests/ui/conftest.py`
  checks that none is left (`tests/ui/layout_items.py`).
