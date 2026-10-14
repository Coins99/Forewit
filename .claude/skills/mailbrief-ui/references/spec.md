# MailBrief UI specification

## Tokens

Each `ColorScheme` has a dark and a light set (`tokens_for(mode, scheme)`), and MailBrief
applies the one that matches the system's mode unless the owner keeps one (see Changing
scheme and mode):

- **Studio** is the default (`DEFAULT_SCHEME`): white panes, Studio Mist grey chrome and
  cards, one blue, after the owner's Apple style reference. `DARK` and `LIGHT` are its
  sets. Its values and the decisions behind them are in `docs/ui/studio.md`.
- **Slate** is grey-blue, from the Liquid Glass prototype.
- **Graphite** is the table below, measured from the first mockup
  (`docs/ui/mockup-three-pane-dark.png`).
- **Ocean**, **Sage**, **Plum** and **Rose** keep Slate's lightness and change its hue,
  for the neutrals and the accent alike. Warning colours stay amber in those.

In every scheme and both modes, text tokens meet WCAG AA (4.5:1) on `panel`, `selection`
and `card`, as do `warning_fg` and `accent_fg` (bare labels) and `on_primary` on
`primary_fill`; `tests/ui/theme/test_theme.py` checks this.

| field | Graphite dark (measured) | Graphite light (provisional) | used for |
|---|---|---|---|
| canvas | #151515 | #f5f4f0 | header bar |
| panel | #1a1a19 | #ffffff | window, panes, cards |
| selection | #151515 | #f5f4f0 | selected row and nav item, hover |
| hairline | #313130 | #e3e2dd | dividers, card borders |
| border_strong | #484847 | #c9c8c2 | check box rims, strong cards, scroll bar handles, the palette field |
| accent_border | #193567 | #9dbbe8 | selected-row bar |
| text | #f0efec | #1f1e1b | titles, body |
| text_secondary | #c3c2b8 | #5f5e5a | senders, meta |
| text_muted | #898782 | #6f6e69 | section labels, notes |
| warning_fg / warning_bg | #d19633 / #2e1b04 | #8a5a00 / #fbefd9 | deadlines |
| accent_fg / accent_bg | #7aa5e6 / #0b1f40 | #1d4f91 / #e3edfb | links, labels, focus / ticked boxes |
| card | #151515 | #f5f4f0 | cards (a fill, no border) |
| primary_fill / on_primary | #0b1f40 / #7aa5e6 | #e3edfb / #1d4f91 | the one primary button, ticked boxes |

The six earlier schemes' `card` is their `selection`, and their primary is their
`accent_bg` with `accent_fg` text, so they keep their look; Studio's primary is solid
blue with white text.

Metrics (logical px): RADIUS 10 (rows, selections), CARD_RADIUS 18, SHEET_RADIUS 12 (the
pages' sheet and a dialog's), DIALOG_GUTTER 8, DIALOG_MARGIN 22 (see Dialogs), PILL_RADIUS
14 (the command palette's search field), TAB_RADIUS 12, FIELD_RADIUS 8, SIDEBAR_WIDTH 128, TEXT_PX 13,
READING_PX 14 (an email's summary), SMALL_PX 12, CAPTION_PX 11, HEADING_PX 17 (the brief's
day, Semibold), TITLE_PX 20 (page and email titles, Semibold); list items pad
ITEM_PAD_X 10 by ITEM_PAD_Y 5 with no selection mark (ITEM_MARK_PX 0).

Fonts: bundled Inter 4.001 Regular, Medium and SemiBold (`ui_font(px, medium=...,
semibold=...)`, never both). From 15 px up, `ui_font` tracks letters tighter by Inter's
dynamic metrics (`tracking(px)`: −0.13 px at 15, −0.33 at 20), as Apple sets display
type; text sizes keep Inter's own spacing.

### Glass tokens

`glass_for(mode, scheme)` holds the Liquid Glass colours, taken from the planner's
prototype, for every scheme. `GLASS_DARK` and `GLASS_LIGHT` are Studio's. Studio's glass
follows WWDC25's Liquid Glass over a fluid colour field (`docs/ui/studio.md`): a flat
backdrop (both stops Studio Mist) with no streak, sheen or lit edge (alpha 0, a light
turned off), and three soft pools of sage, amber and rose light (`pool_a`, `pool_b`,
`pool_c`) that show through its frosted chrome. Its pills are lit as a Liquid Glass tab
is (measured on one): `highlight` (light on the rim along the top and the bottom), `lens`
(the glow that light lenses into the body from both long edges), `end_shade` (the ends,
where that light never reaches), `edge` (an even contact ring, light mode only) and
`shadow` (a soft shadow below, light mode only). Every pill is frosted (`pill`) and lit
at rest, wherever it sits, and brightens under the pointer (`pill_hover`). Its selected page is a vibrant white fill with no rim
(`raised_rim` alpha 0: no glass on glass), and its primary pill is solid blue, labelled
in `primary_text` (white). The other schemes' pools and layers have alpha 0, their
`pill_hover` too, and their `primary_text` is their `text`. Glass belongs to navigation
chrome only (header, sidebar, a dialog's frame) and pill buttons. Lists and the reading
pane stay opaque.
A translucent colour is a `Tint`: an opaque `#rrggbb` and a separate alpha, never an
eight-digit hex. Glass lies over a backdrop drawn from `backdrop_top`, `backdrop_bottom`
`streak` and the pools, so contrast can be proven. `tests/ui/theme/test_glass.py` checks
the worst case over the backdrop's darkest and brightest pixels (either stop, under the
streak and under every combination of the pools), under full sheen, shade or hover fill,
at the tint's alpha and at `min_tint_alpha`:

- `text` and `text_secondary` on a glass panel reach 4.5:1.
- `text` on the raised pill and on pill buttons, and `primary_text` on the primary
  pill, reach 4.5:1, over glass, `panel`, `card` or `opaque`.
- `accent_fg` (the focus ring) beside glass reaches 3:1.
- The opaque fallbacks reach 4.5:1 for text on `opaque`, and 3:1 for `rim_contrast`
  on `opaque` and `panel`.

`text_muted` fails on glass in both modes, so it never sits there.

### Glass painting (`ui/glass/`)

**Chrome** (`workspace.GlassChrome`): the header and sidebar have transparent stylesheet
backgrounds and paint a glass panel over the part of the workspace's backdrop behind
them.

- The workspace asks its renderer for a backdrop on every resize and theme change,
  keyed by its size, scheme and mode, and repaints both when it arrives.
- The selected page is `paint_raised`. A hovered or focused sidebar button gets the
  raised fill at `ACTIVE_RAISE` strength, with no rim.
- Muted labels inside the sidebar (the note) take `text_secondary`, because `text_muted`
  fails contrast on glass.
- The sidebar's Google sign-in and Settings' Disconnect are glass pills.

- **Backdrop** (`backdrop.py`): a diagonal gradient from `backdrop_top` to
  `backdrop_bottom`, under five streaks of light, then three pools of colour (`POOLS`:
  radial pools of `pool_a`, `pool_b` and `pool_c`, at the top left, the right and low
  down, where the chrome shows them). The streaks' strengths scale `streak`'s peak
  alpha, and their positions stretch with the window's width. Only the blurred copy is
  ever seen, so it is painted at `SCALE` (a quarter) of the window's logical size and
  blurred by halving three times and scaling back up. A backdrop depends only on
  `BackdropKey`: logical size, scheme and mode.
- **Renderer:** `BackdropRenderer.request(key)` returns a cached backdrop at once, and
  with no asyncio loop running it renders one at once.
  - Otherwise it renders on a one-thread worker pool, and the result returns by a queued
    Qt signal, never an asyncio task. Under qasync, a task created while the smoke check
    built its window never ran, and shutdown waited for it forever. `ready` then emits
    the result.
  - Requests made during a render become one render of the last of them, and a render
    that finishes after a newer request is cached but not emitted.
  - A failed render is logged, and a later request tries again.
  - It keeps the last four backdrops. `drain()` finishes the render in progress at
    shutdown.
- **Painters** (`painter.py`), with `current_glass()` at paint time:
  - `paint_panel` fills `opaque` first, so a backdrop that doesn't yet reach a growing
    panel's edge never leaves it see-through. Then it shows the blurred backdrop behind
    `area`, which is the panel in window coordinates, then the tint, the sheen over the
    top 120 px and a lit top edge.
  - `paint_raised` is a fill with a sheen, never a second layer of glass. Its rim is lit
    from `raised_rim`; alpha 0 draws none (Studio: Apple's selected controls have no
    rim), except under increased contrast.
  - `paint_pill` is a fill, lighter in its top half, shaded in its bottom half. A pill
    that isn't primary fills with `pill_hover` at the hover's strength (Studio: a soft
    ink or white fill, as apple.com's buttons fill on hover; the other schemes' is 0).
  - `paint_rim` is 1 device pixel wide. `hairline.crisp_rect` snaps it onto the device
    pixel grid through the painter's device transform, rounding inward to the first
    and last whole pixels inside the widget's clip. It stays one full pixel at 125% and
    150% at any position. `HairlineFrame` (cards) uses the same helper.
  - `GlassMode.REDUCED_TRANSPARENCY` makes panels `opaque` and drops the backdrop and
    sheens. `INCREASED_CONTRAST` does the same and draws every rim in `rim_contrast`.
- **Pill buttons** (`button.py`, `GlassButton`, made by `outline_button(..., glass=True)`).
  Every button in MailBrief looks like Settings' Disconnect: a pill of glass at rest,
  wherever it sits. A `GlassButton` paints itself; `ThemeStyle` draws every other push
  button as the same pill (see Buttons, lists and tabs).
  - **Size:** its label (and icon) plus `PAD_X` (10 px) each side and `PAD_Y` (6 px)
    above and below, plus `ROOM_X` (3 px) each side and `ROOM_Y` (3 px) above and below,
    room for the pill to grow into on hover and for its shadow. Its label is 13 px Medium
    (`outline_button(px=...)` keeps Medium for glass). Keep `PAD_X` at 10: 12 widens the
    content past the 680 px width tests.
  - **Icon:** plain, as the label is: `outline_button(icon_name=...)` sets a bundled icon
    tinted in `text` (through `follow_theme`), drawn at the leading end, then `ICON_GAP`
    (6) and the label. Never put an icon in a tile or square. Sync and review (refresh),
    Draft a reply (pencil) and Open in Gmail (external-link) have icons.
  - **Light:** with Studio's layers lit (`highlight` alpha above 0), `paint_liquid_pill`
    paints every pill as glass at rest, wherever it sits: frosted (`pill`) with its
    highlight, lens and shadow, and `pill_hover` over the body as the hover rises. Its
    light follows a Liquid Glass tab's, measured pixel by pixel: light enters along the
    two long edges, as a crisp 1 device px rim line along the top and an almost equal one
    along the bottom (`RIM_LIGHT`, a profile down the pill's height), each glowing into
    the body and falling steeply within a point, then faintly over about six
    (`LENS_LIGHT`; the middle is darkest). Round the ends the light fades out and the
    outermost edge is shaded (`END_SHADE`, by distance in from each end as a share of the
    radius: darkest at the end, gone by one radius). The primary's glow stays off its
    body, so its white label keeps its contrast. The shadow (`SHADOW_RINGS`) falls below
    the pill only and outside it. The other schemes paint `paint_pill`.
  - **Hover:** animates in, as Apple's glass lights from within on interaction.
    - The pill grows `GROW_X` and `GROW_Y` each side on an OutBack spring (260 ms),
      whose overshoot still fits the room.
    - `paint_glow` lights a band within `GLOW_WIDTH` (3 px) inside the edge. It starts
      under the pointer and spreads round the pill as the animation runs.
    - The rim's `specular` light fades in and follows the pointer, and the body fills
      with `pill_hover`. Studio has no coloured light (specular alpha 0): only the fill
      and the spring.
    - Leaving eases it all out (OutCubic, 200 ms). With motion reduced, the light comes
      at once and the pill never grows. The solid modes paint no light.
    - The body is never lit, and labels sit at least `PAD_Y` (6 px) inside the edge,
      clear of the glow. Lighting the body would cut the primary pill's text contrast
      below 4.5:1 in dark mode.
  - **Press:** shrinks it to 0.97 in 90 ms, and release springs it back with OutBack in
    280 ms. Both are instant when motion is reduced. They follow the button's `pressed`
    and `released` signals, so a keyboard press (Space) shrinks it too. Qt emits
    `released` when a held button is disabled, and disabling also drops the hover.
  - **Focus:** a 2 px `accent_fg` ring inside the pill's edge, only after keyboard
    navigation (`hairline.keyboard_focus`), like the app's other focus rings; a click
    gives no ring.
  - **Disabled:** the body at half opacity, with `text_muted` text.
  - **Text:** painted with `drawText` and mnemonics hidden, after `button_label`
    escaping.
  - **Where:** the header's Sync and review (primary) and Cancel, the sidebar's Google
    sign-in, Settings' Disconnect and Sign in with Google, and the Today page's card and
    footer buttons. Every other
    button is a `ThemeStyle` pill that looks the same at rest.
- **Preferences** (`preferences.py`): `glass_preferences()` holds the `GlassMode` and
  reduced motion that widgets read at paint time. `effective_preferences` sets them from:
  - the system (`system.py`): Increase contrast (Qt's `contrastPreference`, or macOS
    NSWorkspace), Reduce transparency (NSWorkspace, or Windows' `EnableTransparency`),
    and Reduce motion (NSWorkspace, or Windows' `SPI_GETCLIENTAREAANIMATION`). A failed
    read is no preference.
  - the owner's choices in Settings > Appearance: Glass (Follow the system, or Always
    solid) and Motion (Follow the system, or Reduce motion). They can add solid glass or
    stillness but never undo the system's, and increased contrast always wins.
- **`GlassAccessibility`** (`accessibility.py`, in `MainWindow`) applies them at once and
  again whenever MailBrief becomes the active app (after System Settings, say) or Qt
  reports a contrast change. It connects to the application's signals; a filter on the
  application would see every event of every object. A change repaints every widget
  inside `paused_collection()`.
- **The Appearance tab** shows a note when the system makes glass solid or still.
- **Paint profiling** (`profile.py`): with `MAILBRIEF_PAINT_PROFILE=1`, the `@profiled`
  paint methods are timed (GlassButton, HeaderBar, SidebarNav, SidebarPage and
  SidebarButton). `run_desktop` logs one line per kind at quit: count, p50 and p95 in
  ms. Without the variable, a paint costs one check. Measured costs are in
  `docs/ui/liquid-glass.md`.
- **Tests:** `tests/ui/glass/` checks that every backdrop pixel stays inside the bounds
  `test_glass.py` proves contrast over. It also measures text contrast on painted pixels
  over each backdrop's darkest and brightest spots, rims at 1x and 2x, and the renderer's
  thread, cache and stale results. `test_gallery.py` saves a glass sampler for each
  scheme and mode as `glass-{scheme}-{mode}-{dpr}x.png` when `MAILBRIEF_UI_SHOTS` is set.

### Changing scheme and mode

The owner picks a scheme in Settings > Appearance (`ui/appearance_view.py`): one radio
button per scheme, Studio first, each with a swatch of its panel in the mode in effect
ringed with its accent. Arrow keys move between them. Below it, Mode: Follow the system
(the default), Light or Dark (`ModeChoice` in `ui/appearance.py`). A choice applies at
once, whatever else is running: `MainWindow._change_scheme` keeps the mode in effect,
`_change_mode` applies the kept or the system's mode (`theme_mode`), and while the owner
follows the system, Qt's `colorSchemeChanged` re-applies the theme
(`_system_mode_changed`). `system_mode(app)` reads the system's mode, light when it
doesn't say. A failed theme puts back what was there and shows "The colours couldn't be
changed." `AppearanceWriter` saves the choices to `appearance.json` on a one-thread
worker; an unknown mode reads as Follow the system. It never uses an asyncio task, which qasync
once never ran. A failed save comes back through a queued signal, and shutdown waits for
the write. Startup applies Studio in the system's mode, then reads `appearance.json` once,
once the data folder is locked: it applies the saved scheme and mode and hands the choices
to the window (`saved_appearance`).

`apply_theme` sets the tokens before it changes the palette. Widgets that read
`current_tokens()` at paint time need nothing more. A widget that keeps a colour, such
as an icon tinted when it was made, sets it through `follow_theme(widget, apply)`. That
runs `apply` now and again on every `PaletteChange` or `StyleChange`, because a hidden
widget gets only `StyleChange`.

## Hairlines (`ui/hairline.py`)

All read colours from `current_tokens()` at paint time.
- `HairlineFrame(QFrame)`: a card (`radius=CARD_RADIUS`, `strong=False`), contents
  margins 16, 14, 16, 14. Paints a rounded `card` fill, with no border or shadow; a
  `strong` card also has a cosmetic `border_strong` border (one device pixel at any
  scale). The border is snapped by `crisp_rect`, and the fill reaches the outer edges of
  those pixels, so straight edges stay crisp at 125% and 150%.
- `HairlineDivider(QWidget)`: horizontal or vertical, 1 logical px, cosmetic line, no
  antialiasing.
- `HairlineSplitter(QSplitter)`: `handleWidth(5)` so it can be grabbed; its handle fills
  with panel and draws one cosmetic hairline at its centre, stopping `SPLITTER_INSET`
  (12) short of both ends so it never meets the sheet's edge.
- `paint_focus_ring(painter, rect, state, color, *, inset=..., radius=RADIUS)`: the
  focus ring of the sidebar, brief list, shortlist, Actions and Your day delegates, a
  rounded 1px cosmetic ring round the row's selection (`SELECTION_INSET_X` 6 and
  `SELECTION_INSET_Y` 2 in from the row, defined here and shared with `paint_selection`);
  the sidebar's follows its pill (`inset=(1, 2)`, `radius=None`). It paints only when
  `keyboard_focus(state)` is true (focus that the keyboard moved, not focus on first
  show).
- Nothing has a sharp corner: cards, the sheet, selections, focus rings, buttons, fields,
  tabs, menus, combo box lists and tooltips are all rounded, and no two hairlines meet.

## Brief list (`ui/brief_list.py`)

- Rows: a header row (from `SECTION_TITLES`) whenever the digest section changes, then
  item rows in digest order. `BriefRow(kind, title, item, sender, chips)`.
- Sender: the contact's name, else its address (`sender_text`). The review step and the
  detail pane use `sender_with_address`: "Name <address>", or the address alone when the
  name is empty, is the address (ignoring case) or contains "@", so a display name can
  never pass for someone else's address.
- Deadline chip (WARNING), its text in the digest's zone (as the detail pane shows it),
  `deadline_chip(item, zone, today, owner_zone)` with the owner's current local day as
  today (`build_rows(digest, proposals, today, owner_zone)`, from `show_digest`), whatever
  day the brief covers. "Within the week" is judged by the deadline's date in the owner's
  zone: DATETIME from today to today + 6 days `Due {local:%a %H:%M}`, any other DATETIME
  (past ones too) `Due {%b} {day} {%H:%M}`;
  DATE `Due {%b} {day}`; UNRESOLVED `Due ` + the phrase cut to 24 characters at a grapheme
  boundary (`cut_text`), with "…";
  NONE no chip.
- Proposal chip (ACCENT), from the first pending proposal: NEW_DEADLINE "Proposes a new
  deadline"; CANCELLED or DELIVERED "Proposes completing an action".
- Model: `KIND_ROLE = UserRole + 1`, `ROW_ROLE = UserRole + 2`. Display is the header
  title or the subject ("(no subject)" when empty). Accessible text is the header title
  or "{subject}, from {sender}" plus the chip texts. Header rows are `NoItemFlags`; item
  rows are enabled and selectable.
- Delegate (`drawText` only): header rows 12px Semibold text_secondary at left 14, 16
  above and 4 below. Item rows padded 10 vertical, 14 horizontal: a selected row gets a
  rounded selection fill (radius `RADIUS`) inset 6 from its sides and 2 from its top and
  bottom, with no accent bar; title 13px Medium (text); sender 13px (text_secondary);
  chips after a 4px gap as bare 11px Medium text in warning_fg or accent_fg, 12px apart,
  with no fill; a cosmetic hairline at the bottom, inset 14 to line up with the text;
  with focus, a 1px cosmetic accent_fg rect inset by 1. Title and sender elide to the row
  width minus 28.
- Shared with the run page's shortlist and the Actions page's rows: `paint_selection`,
  `paint_lines` (a title and a `subtitle`: a sender or a meta line), `paint_chips`,
  `paint_separator`, `row_height`, `elided`, `flat`, `SINGLE_LINE`, `ROW_PAD_V`,
  `ROW_PAD_H`, `ELIDE_MARGIN`, `CHIP_GAP`, `SELECTION_INSET_X` and `SELECTION_INSET_Y`;
  the focus ring is `paint_focus_ring()` from
  hairline.py. Never copy painting code: paint new rows with these.
- View: objectName `briefList`, accessible name "Brief items", NoFrame,
  ScrollPerPixel, SingleSelection; `item_selected(DigestItem)`; `show_rows(rows)` selects
  the first item row; Return and Enter emit `activated` once and are consumed.
  While the whole list fits (its vertical scroll bar has no range), Page Up and Page
  Down (no modifier) scroll the detail pane by a page (`set_page_scroll`, set by the
  workspace); when the list scrolls, they page the list. The rule is `page_detail()`,
  shared with the Actions page's lists.

## Wrapping labels (`ui/labels.py`)

`WrapLabel(QLabel)`, made with `wrap_label(text, *, tone=None, px=None, medium=False)`
(like `plain_label`): plain text laid out with `QTextLayout` and
`WrapAtWordBoundaryOrAnywhere`, so an unbroken token (an address, a link, a long word)
wraps instead of widening its container. Line breaks in the text are kept, and the layout
(never `text()` or the accessible name) gets a zero-width space after every `@` and `/`,
so an address wraps after its `@` and a path after a slash before anywhere else.
`hasHeightForWidth()` is True and `heightForWidth(w)` includes the contents margins;
`sizeHint()` is at most 40 average characters wide with its matching height;
`minimumSizeHint()` is 0 wide and one line high. Layouts are cached per width for the
current text and font, at most 8 widths; a change of text or font, even by
`QLabel.clear()`, drops them. It paints in the palette's
`WindowText`, so the stylesheet's `tone` colours apply. `setText` keeps `text()` and makes
the whole text the accessible name; no tooltip, no links.

## Detail pane (`ui/brief_detail.py`)

`BriefDetailPane(QScrollArea)`, objectName `briefDetail`, accessible name "Selected
email". Signals: `suggestion_requested(str, int)` (ACCEPT or DISMISS, from this module),
`accept_into_requested(int, str, int)`, `proposal_requested(str, int, int)` (APPLY or
DISMISS), `reply_requested(str, str)` and `source_requested(str)`; it never opens URLs
itself. `rebuilt()` follows each `show_item` and `show_empty`, and `buttons()` lists the
content's buttons in display order. The pane has `ClickFocus`: Tab goes from the brief
list straight to its buttons, and a click still lets the arrow keys scroll it.
Its content is at most 720 px wide (`DETAIL_MAX_WIDTH`) and stays at the left on wide
windows. Content margins 16, 20, 16, 20, spacing 10, in order (every mail- or AI-derived
line is a `WrapLabel`):
1. Subject (or "(no subject)"), `TITLE_PX` (20px) Semibold.
2. Sender, secondary, `sender_with_address` (objectName `detailSender`). No received
   time is invented.
3. Summary, `READING_PX` (14px).
4. Action text, secondary, if present and different from the summary.
5. Deadline: 14px clock icon (warning_fg) and a warning label, `Due ` + `deadline_text`
   in the brief's zone (see Dates).
6. `Continues: “{title}”`, secondary, for each link that isn't a source.
7. Suggestions. PENDING: a `HairlineFrame` card with the title (13px Medium), a 12px
   secondary meta line ("Yours." or "Waiting for someone.", then the target with its
   reason, else the deadline, else "No deadline stated."), the plan's steps in order
   (`· {step}`, 12px secondary), Accept and Dismiss, and one `Add to “{title}”` button
   per link. ACCEPTED: `Accepted: {title}`, secondary, with no plan.
   DISMISSED: nothing.
8. Each pending proposal: a card with `Proposes for “{action_title}”: “{evidence}”`, an
   `effect_text` button (APPLY) and Dismiss.
9. Footer: "Draft a reply" (pencil) and, only for https links on mail.google.com,
   "Open in Gmail" (external-link).
10. A stretch.
Buttons are made with `outline_button(..., glass=True)` (`button_label()` text,
`setAutoDefault(False)`) and have an objectName: `acceptButton`, `dismissButton`,
`addToButton`, `applyProposalButton`, `dismissProposalButton`, `replyButton` and
`openInGmailButton`.

## Actions page (`ui/actions_view.py`, `ui/action_detail.py`, `ui/action_text.py`)

`ActionsPanel` (objectName `actionsPanel`) is the `actions` page, edge to edge like Today:
a `HairlineSplitter` (stretch 4 : 5, children not collapsible) holding the list side
(`actionsListPane`, at least 220 px) and `ActionDetailPane` (at least 280 px).
- List side, top to bottom: "Your actions" (20px Semibold, `actionsHeading`, margins
  14, 14, 12, 4), with New task; the project filter (at least 7 characters) and
  "Projects & areas", 6 apart; `tabs`, a `QTabBar` (`actionsTabs`, "Action views": "Open (N)",
  "Waiting (N)", "Completed (N)" or "(200+)", no base, not expanding, `StrongFocus` so Tab
  reaches it on macOS too); `stack`, a `QStackedWidget` (`actionsStack`) holding the three
  `lists[view]`. `show_view` sets the tab bar's index and the stack; a click on a tab
  moves the stack; `view()` reads the tab bar.
- Each list is an `ActionList` (an `ActivatingList`; objectName `actionList`, accessible
  name "{Open|Waiting|Completed} actions"; NoFrame, ScrollPerPixel, no horizontal scroll
  bar) painted by `ActionRowDelegate`. Each item's text is exactly `describe()`'s, and
  it carries `action_row()`'s `ActionRow(title, meta, chips, muted)` under `ROW_ROLE`
  (brief_list's). Page Up and Page Down page the detail while the list fits
  (`page_detail`).
- `ActionRow`: meta joins, with " · ", "Completed Tue Oct 6" (in the owner's zone), the
  target ("Target Fri Oct 2", never its reason: the detail gives that), "Due
  {deadline_text}" ("Due Mon Oct 5") and "2/5 steps", and nothing else (the detail pane
  shows "Carried over" and "Source no longer in local mail"); with none, "No target or
  deadline". Chips: "Overdue" (WARNING,
  open actions only), "N new in thread" (ACCENT, while new messages are unseen) and
  `proposal_chip` for pending proposals. Completed rows are muted: title text_secondary,
  meta text_muted.
- `ActionRowDelegate`: the brief list's row, from the shared helpers only; painting is
  clipped to the row; `sizeHint` is `QSize(0, row_height(has_chips))`, so a row never
  widens the page.
- `action_details()` (`ui/action_text.py`) works out an action's dates, progress, state,
  thread activity and pending proposals once; `describe()`, `action_row()` and the
  detail pane all read it, so they never disagree.
- `ActionDetailPane(QScrollArea)`: objectName `actionDetail`, "Selected action",
  `ClickFocus`, content (`actionDetailContent`) at most 720 px wide at the left, margins
  16, 20, 16, 20, spacing 10. Its seven buttons are made once and stay (the panel exposes
  them under its old names), so only the text between them is rebuilt. In order, every
  mail-, AI- or owner-written line a `WrapLabel`:
  1. Title, 20px Semibold (`actionTitle`).
  2. State, secondary: "Yours · open", "Waiting for someone · open" (either with " · carried over" while it
     applies) or "Completed Tue Oct 6".
  3. "Due {deadline_text}", warning with " · Overdue" when overdue, else secondary
     (`actionDeadline`); the target with its reason while it is still the suggested one
     ("Target Fri Oct 2, one working day before the deadline"), secondary
     (`actionTarget`).
  4. "Plan · 2 of 5 done" (12px muted heading), then each step in order: "✓ {text}"
     muted when done, "○ {text}" otherwise.
  5. "Notes" and the notes.
  6. "Thread" and `describe()`'s activity lines, first letter capitalised ("2 new in
     thread, latest Tue 14:02 from Sam", "You replied Sun Sep 20"); then `seenButton`
     (Mark seen).
  7. A sentence about the pending proposals; then `proposalsButton` (Proposals…).
  8. "Source" or "Sources": each subject ("(no subject)" when empty), then 12px
     secondary "{address} · Received Fri Oct 2" (`day_text`), plus " · No
     longer in local mail". `sourceButton` (Open source, external-link) follows the first
     Gmail source.
  9. Footer: `editButton`, `completeButton` (Complete, or Reopen on Completed),
     `deleteButton`, `draftButton` (pencil, with the draft menu).
  Empty states, muted (`actionEmpty`), with no buttons: Open "No open actions. Accept a
  suggestion in a brief to start one.", Waiting "Nothing you're waiting for.", Completed
  "No completed actions yet."
- Tab: the tab bar, the list shown, then the detail's buttons in display order.
- Behaviour is unchanged: `_update_buttons` keeps its rules, every button emits
  `action_requested` or `draft_requested` for MainWindow's `start()`, and only Gmail
  links open.

## Dates (`ui/deadline_text.py`)

One style across the window, as the brief reads: every date or time a person sees uses
`day_text` or `moment_text`, in the owner's zone. Only the action editor's date fields
stay ISO, since dates are typed there, and the CLI is unchanged
(`git grep -nE "isoformat\(\)|%Y-%m-%d" src/mailbrief/ui` lists only `action_editor.py`).
The brief list's chips keep their shorter forms (see Brief list).
- Weekday and month names are the fixed English `WEEKDAYS` and `MONTHS` of
  `deadline_text.py`, never `%a` or `%b`: Qt applies the system language to C date
  formatting, so a French Mac would otherwise print "lun." and "août". No file anywhere in
  `src/mailbrief` uses a strftime directive that follows the system language (`%a`, `%A`,
  `%b`, `%B`, `%p`, `%c`, `%x`): `tests/unit/test_source_rules.py` fails, naming the file and
  line, if one appears. Wording a person reads therefore lives in `ui`, which can use these
  helpers; services keep ISO dates (`coverage_line`, which the CLI prints).
- Qt's own widgets take their month and weekday names (the calendar pop-ups of Saved mail
  and the action editor) from the default `QLocale`, which follows the system language.
  `use_english_locale()` in `app.py` sets it to English, and `app.main()` calls it right
  after the QApplication exists: a widget takes the default locale when it is created, so
  no widget may be created before it. The date fields keep their explicit `yyyy-MM-dd`
  display format; English is en_US, so the calendars start the week on Sunday.
- `today` (the owner's day, in the owner's zone) is a required keyword argument of every
  formatter below; the window passes its injected clock's day.
- `weekday(day)`: "Mon". `month_day(day, *, today)`: "Oct 5"; when the year isn't
  `today`'s, "Jan 4, 2027". `day_text(day, *, today)`: "Mon Oct 5" or "Mon Jan 4, 2027".
- `moment_text(moment, zone, *, today)`: "Thu Oct 8, 17:00" in `zone`, the year as
  `day_text` adds it.
- `deadline_text(item, zone, *, today)`: DATETIME `moment_text` in the owner's zone,
  DATE `day_text` of the day the email names, UNRESOLVED the email's phrase in quotes,
  NONE None. The brief detail's deadline, suggestion meta lines, proposal effects ("Set
  the deadline to Mon Oct 5") and the Actions page all use it; a deadline in another year names it.
- Thread activity in the past week reads by weekday ("Tue 14:02", "Tue"); older, by
  `day_text` with the owner's day ("Sun Sep 20, 10:30", "Sun Dec 20, 2026, 15:30").
  Proposal rows show when the email arrived with `moment_text`.
- Everywhere else a date appears it is the same style, with the owner's day (so a day in
  another year names it): Today's coverage footer (`coverage_short` in `ui/workspace.py`:
  "Inbox on Oct 6, up to 09:14", "Inbox on Dec 31, 2025, up to 09:14"); the Briefs page's
  saved-brief rows ("Thu Sep 3 · Complete · 1 item · …"), missed days ("Fri Sep 4 · no
  brief") and the Replace sentence; MainWindow's "Viewing the brief for Thu Sep 3." banner
  and the "Showing the brief for …" and "the Inbox for …" status lines; Drafts' "updated
  Mon Sep 28, 10:05" and the draft editor's version times; the Preferences
  automatic-analysis line ("since Tue Sep 29"); the Data dialog's backup time ("Valid
  backup from Thu Oct 8, 17:00", in the owner's zone, set the way `cached_dialog.zone`
  is), and a completed action ("Completed Tue Oct 6").
- An action row's second line is only the completed day, target, deadline and steps; "Carried
  over" and "Source no longer in local mail" are the detail pane's, not the row's.

## Workspace (`ui/workspace.py`)

- `HeaderBar`: objectName `headerBar`, margins 16, 8, 16, 8; "MailBrief" 15px Semibold
  (`TITLE_BAR_PX`), a stretch, a 16px refresh icon (text_secondary), the status and the window's widgets.
  The status is an `ElidedLabel` (12px, secondary, `headerStatus`): it asks for its text's
  width but shrinks to nothing, so a long status never widens the header; its accessible
  name is the whole text. `set_status(text)`; an empty status hides the refresh icon,
  which keeps its space. `add_widget(widget)` appends widgets right of the status,
  spacing 8.
- `SidebarNav`: objectName `sidebar`, fixed width `SIDEBAR_WIDTH`, margins 8, 12, 8, 12,
  spacing 2. A fixed-height `QListView#sidebarNav` with pages in Gmail's and Outlook's
  order (`PAGES`): today (Today, sun), inbox (Inbox, mail), actions (Actions,
  checkbox), waiting (Waiting, hourglass), drafts (Drafts, pencil), briefs (Briefs,
  calendar), then, set apart by a 12px gap with a hairline across it (`GROUP_STARTS`),
  calendar (Calendar, calendar-week). A click in the gap chooses nothing. Ctrl+1 to Ctrl+7 follow this order
  (`PAGE_SHORTCUTS`). Rows 32px: the selected row is a raised pill on the glass, which
  the list (`_NavList`) paints under its rows so it can glide from the old page to the
  new one in `GLIDE_MS` (420 ms) on `fluid_curve()` (`ui/glass/motion.py`, monopo saigon's
  expo-out, cubic-bezier(0.19, 1, 0.22, 1)); with motion reduced it moves at once;
  a 16px icon at x 10 (text when selected, else text_secondary); a 13px label; the count
  right-aligned in text_secondary; the brief list's focus ring. Below: a stretch, then
  `NavButton` rows painted like page rows: `saved_mail` (Saved mail, mail, "Saved &mail"),
  `data` (Data, database, "&Data", accessible name "Data and recovery") and `settings`
  (Settings, settings, "Se&ttings"); an 11px muted note, hidden while empty; then
  `footer`, a QVBoxLayout (spacing 4) for the window's widgets. `page_requested(str)` (on
  selection, a click and Return/Enter; for `DIALOG_PAGES`, only a click or Return),
  `settings_requested()`, `set_counts(actions, waiting, drafts)` (None hides a count; a
  row with a count is read as "{label}, {count}", one without as its label),
  `set_current(key)`, `current_key()`, `set_note(text)`.
- `NavButton(text, icon_name, *, mnemonic=None, accessible_name=None)`: with a mnemonic
  (app-authored text such as "Se&ttings") Alt and that key press it; it still paints
  `text`.
- `BriefHeading` (`briefHeading`), at the top of the list pane, margins 14, 16, 14, 4,
  hidden until a brief is shown: `title` (`HEADING_PX` 17px Semibold, `brief_title`: "Tue Oct 6", plus
  " · Partial" or " · Empty") and `meta` (11px muted, elided, `brief_meta`): "{account} ·
  saved HH:MM" ("saved Oct 7 HH:MM" when saved on a later day in the brief's zone), plus
  " · N failed", " · N deferred" and " · Inbox sync incomplete". The save time is in the
  brief's own zone, like its coverage footer, with that zone named in parentheses when the
  owner's zone would show a different time. Its accessible name is the full sentence:
  account, save time in the brief's zone, the coverage counts and whether the Inbox sync
  was complete.
- `ThreePaneWorkspace`: objectName `workspace`. One sheet of glass: HeaderBar, then
  SidebarNav beside `sheet` (`ContentSheet`, `contentSheet`), then `footer_bar`
  (`FooterBar`, `footerBar`, which holds the window's status strip), with no dividers
  between them. The glass's sheen runs from the window's top across them all, and only
  the header has the lit top edge. `sheet` paints the glass, then a `panel` sheet with
  `SHEET_RADIUS` (12) corners and a cosmetic `hairline` edge, leaving `SHEET_GUTTER` (8)
  of glass at its right; `pages`, a QStackedWidget, fills it 1 px inside the edge and is
  masked to a corner one pixel smaller (a mask is aliased; its steps fall on the sheet's
  antialiased fill), so no page's square corner shows. Page `today`
  (`todayPage`): `today_top` (a QVBoxLayout for banners) above a `HairlineSplitter`
  holding the list pane (the heading, `BriefListView` and an 11px muted coverage footer,
  margins 14, 10, 14, 10: one elided line of `coverage_short(digest, today)`, with the
  services' `coverage_line` as its accessible name) and the `BriefDetailPane`; stretch
  4:5, not collapsible, minimum widths 220 and 280. `add_page(key, widget)`,
  `show_page(key)` (KeyError for an unknown key) and `current_page()`.
  The detail's scroll position, kept when the same brief is shown again, waits until the
  new content's range reaches it (wrapped labels can grow the range over several layout
  passes); scrolling by hand first drops it.
  `show_digest(digest, *, links, proposals, owner_zone, today)` builds the rows (chips
  counted from `today`), the heading and the coverage, shows "No analyzed messages in this
  brief." when empty, and, for the same saved brief (account, day and save time), keeps
  the selected email and the detail's scroll position. `set_today(today, owner_zone)`
  recounts the list's chips for a new day in place (`BriefListModel.update_rows`,
  `dataChanged`, no model reset) and rewrites the heading and the coverage footer, which
  name a year other than today's, so the selection, the detail pane and keyboard focus
  stay; nothing else in the brief depends on the day. `clear(message)` shows no brief.

## Main window (`ui/main_window.py`)

- Central widget: the workspace alone. `statusStrip` (margins 16, 6, 8, 6, no background)
  sits on the workspace's `footer_bar` glass under the sheet: the status (a 12px
  secondary `WrapLabel`) and an outline Undo. No outer scroll area; the window opens at 1100 × 720 and its minimum
  width stays at most 900 px with the theme applied.
- Header: after a run whose Inbox sync was complete or partial, when that sync finished
  (when the review opened, else when the run ended), in the owner's zone: "Checked Gmail
  at HH:MM" that day, "Checked Gmail Oct 7 at HH:MM" on a later one (`_render_checked`).
  Then outline Sync and review and Cancel. A one-minute `day_timer` calls `_minute_tick`:
  when the owner's day differs from the one the views were built for (`initialize` sets
  it), the check time gains its date, the brief's chips recount in place (`set_today`),
  and `_refresh_for_new_day` reloads the actions (their "overdue", "carried over" and
  weekday labels) and, when it shows, the Briefs page, through `start()`. While another
  operation runs, that reload waits (`_day_refresh_pending`) and is retried each tick.
  `closeEvent` stops the timer.
- Sidebar footer, top to bottom: Saved mail, Data, Settings, then Google sign-in (12px
  glass pill, `connectButton`) only while no account is connected. Rows and buttons are
  muted while disabled.
- Settings' Account card (`accountCard`, a `HairlineFrame` at the top of Connection and
  AI): "Account" (13px Semibold), the Gmail line (`connectionStatus`, 13px `WrapLabel`,
  the window's `connection`) and the AI line (`aiStatus`, 12px secondary, the window's
  `ai`) at the left, read by their text; at the right, centred, D&isconnect
  (`disconnectButton`, the window's `disconnect_button`) with an account, or Sign in with
  &Google (`settingsSignInButton`) without one (`set_signed_in`). The window keeps owning
  what they say and does what they ask; a long address wraps there, never widening the
  dialog. Settings opens even while an operation runs (`_request_settings`): opening only
  reads, so it runs beside the operation (`_settings_reads`, drained at shutdown) without
  touching the status line; the dialog then says `BUSY`, and its fields, Preferences,
  Disconnect and sign-in wait until the operation ends, while Appearance works at once.
- Cancel shows in the header only while an operation runs. `ThemeStyle`, Fusion behind a
  `QProxyStyle`, never underlines mnemonics; Alt and the letter still work.
- Pages: `today` (the "Viewing the brief for …" banner and the retry row in `today_top`),
  `run` (`runPage`, a scroll area, margins 16, 14, 16, 14, holding `runColumn`, a centred
  column at most 760 px wide), `actions` (the Actions page, no margins), `drafts` and
  `briefs` (the panels, margins 12). Today shows `run` while the review or consent
  waits. Actions opens the Open tab and Waiting the Waiting tab;
  changing the tab moves the sidebar. Briefs is a page like the others: choosing it loads
  the saved briefs and missed days through `start()` (refused with a reason while busy or
  before local storage loads), then shows `BriefHistoryPanel` (`ui/history_view.py`): a
  20px Semibold "Briefs" heading, the saved and missed lists, outline Open and Brief this
  day…, and a Replace / Keep it confirmation, each button row ending in a stretch.
  Opening a saved brief returns to Today. An automatic refresh waits while a replacement
  is being confirmed there, and reloads Briefs when it ends.
- Drafts: with no drafts the list is hidden and `draftsEmpty`, a muted, centred, wrapping
  message, fills the page; with drafts the list shows and the message hides.
- Calendar (ADR 0018, `ui/calendar_view.py`): a 20px Semibold heading "Calendar · Oct 12 –
  18" with outline Previous week, This week and Next week; one `ActivatingList` row per day
  heading (Medium, enabled but not selectable, "· Today" on the owner's day) followed by
  that day's all-day events, then open actions' deadline and target markers, then timed
  events by start. Rows are plain item text, never tooltips. `calendarEmpty` (muted) shows
  under the week when it holds nothing; `calendarNote` (muted) explains markers when there
  are some. Outline New event, Open and Delete event below. Mnemonics avoid every
  window-level letter. An event ending at midnight belongs to the day before; when the row
  isn't that day, its end is dated as the clock says ("22:00–Oct 14 00:00"). A refresh
  keeps the selected row (an event, Google event or marker, on the same day when it's
  still there), and a saved event is selected. This week, or moving back to it, follows
  today, so a new week moves the page on. Reading weeks doesn't go through `start()`: while
  another operation runs, the weeks, New event, Open, Open in Google Calendar and Plan with
  AI still work (the newest read of the week wins), and Delete event, Allow and Refresh
  wait. The editor's Save waits too, and its status line says `WAITING` ("MailBrief is
  busy; Save works again once it finishes."). Allow, Refresh and the page's quiet syncs
  can be cancelled, and quitting stops them.
  `EventEditor` is a dialog (title, All day, Starts and Ends as a
  `DateBox`, a `yyyy-MM-dd` date with its calendar popup, and a `TimeBox`, notes) that
  locks while saving, like the action editor. A `TimeBox` is an editable combo box: type
  `HH:mm`, or choose from 15-minute steps; the end's list runs 15 minutes to 24 hours after
  the start, each with the length ("00:00 (9 hrs)"), and times a DST change skips are left
  out. The end's list marks an item only when it is the end itself, so a longer event
  marks none. With the list closed, Up and Down move a time to the next quarter hour
  later or earlier, as they move the date beside it, and Page Up and Page Down by an
  hour; they take a time typed but not yet taken first, pass over times a DST change
  skips and keep the start on its day. The wheel changes a `DateBox` or a `TimeBox` only
  while it has focus: both have the `StrongFocus` policy, so a turning wheel never
  focuses them, and a trackpad's coasting (`ScrollMomentum`) moves nothing. A time the
  fields still show keeps its instant, so the second of a repeated hour stays the second.
  Dates and times typed but not yet taken count as unsaved input.
  Moving the start keeps the length; an end at or before the start, past 31 days or not a
  time is refused, the fields go back, and the status line says why. Save first takes a
  time still being typed, as leaving its field would. The Save that comes with a refusal
  (Return, or the click that took focus from the field) waits, so the reason stays on
  screen; the next Save saves what the fields show, and changing anything else first lets
  it go ahead. The end date's range runs from the start's day to 31 days on (30 for all-day
  events). Ticking All day on an event that ends at midnight ends it the day before, and
  unticking straight away brings its times back.
  Google Calendar (phase 2, one sign-in per ADR 0019): under the heading, `googleStatus`
  (muted) says whether Gmail is connected, whether Calendar is allowed and when the saved
  copy was read, beside outline Allow Google Calendar (only when the sign-in lacks it) or
  Refresh. Google rows end with their calendar's name, can't be opened or deleted, and
  enable outline Open in Google Calendar when they have a Google Calendar link. Outline
  Plan with AI (A) opens `PlanningDialog` (`ui/planning_dialog.py`): a stack of four steps
  (instruction and part boxes; preview lines, privacy notice and first-use consent box;
  waiting with Cancel request; checkable proposal rows of plain item text, Groq's reason
  and overlaps on their own lines). `planningStatus` (warning tone) sits above the stack and
  shows only with a message; outline Close (C) shows on the first two steps, and closing
  while Groq is being asked cancels the request. Proposal rows wrap at the list's width
  (a delegate sizes each from the text area the style paints, inside the row padding
  `ITEM_PAD_X`, `ITEM_PAD_Y` and mark `ITEM_MARK_PX` from `tokens.py`); deletions start
  unticked, and each row's overlaps are worked out again whenever a box changes, as if it
  and the ticked changes were applied. `planningReading` (muted) says Google Calendar is
  being read first when its times are ticked and the saved copy misses some of the four
  weeks. The instruction stays, selected, until changes from it are applied.
- Shortcuts: `QShortcut`s on the window, Ctrl+1 to Ctrl+6 (Cmd on macOS), call
  `_request_page` with today, actions, waiting, drafts, briefs and calendar (`PAGE_SHORTCUTS`,
  `page_shortcuts`), so Briefs refuses as the sidebar does.
- Tab order (`_link_tab_order`, at startup and on each `rebuilt`): Sync and review,
  Cancel, the sidebar's page list, Saved mail, Data, Settings, Google sign-in, then
  the page (on Today: Back to latest, Retry, Your day's three lists, the brief list and the
  detail's buttons in display order), then Undo and back to Sync and review; hidden and
  disabled widgets are
  skipped. The chain is linked from Undo, so other pages' controls, in their own order,
  fall between Today's last button and Undo. Never walk the focus chain from Python
  (`nextInFocusChain()`, `previousInFocusChain()`): PySide re-parents the returned
  wrapper under the widget it was called on, and deleting that widget later invalidates
  the wrapper and all its children.

## Your day (`ui/today_view.py`)

- `DayPanel` (`dayPanel`, "Your day") sits in `today_top`, under the viewing banner and
  Retry, above the brief: three `HairlineFrame` cards (grey `card` fills) with stretch 1
  each, 12 px apart, inside 16 px side, 14 px top and 6 px bottom margins: Needs attention
  (`dayAttention`), Today's agenda (`dayAgenda`) and New replies (`dayReplies`).
- A card: no frame padding of its own, layout margins 4, 12, 4, 8; a top row with its
  heading (12 px Semibold, secondary, "Needs attention") and, at the right, its count as
  a dashboard's figure (`count`, `COUNT_PX` 22 Semibold, "4"; muted at 0; accessible name
  "Needs attention: 4"), then a frameless
  `ActivatingList` that shows at most three rows and scrolls, or a 12 px muted line when
  empty. The heading and the empty line are inset 14 px, in line with the rows' text.
- A row (`DayRowDelegate`): one 13 px line, 6 px above and below, `ROW_PAD_H` (14 px) at
  the sides, clear of the rounded selection; the
  title at the left in `text`, a note at the right in `text_secondary`, or `warning_fg` when
  something is due or overdue. A due row (`warning`) or one with new replies (`accent`)
  has a `DOT_PX` (6) dot of its colour before the note, as a dashboard marks state. The
  title gives way first, down to 40% of the row. Selection and the focus ring as in the
  brief list; no separators.
- Activating a row opens its record: Return, Enter or a double-click, never a single click
  (a double-click would land on the page it opened).

## Command palette (`ui/palette.py`)

- `CommandPalette` (`commandPalette`, "Go to or search"), 560 × 420, opened by Ctrl+K through
  `dialog_guard.show_dialog`, and listed in `_dialog_open()` and the quit path's closes.
- A `QLineEdit` (`paletteField`, a rounded search pill: `border_strong` rim, radius
  `PILL_RADIUS`, padding 6px 14px) over an `ActivatingList` (`paletteResults`) and a 12 px
  muted note (`paletteNote`). Section headings are list rows with `NoItemFlags`, 12 px
  Semibold in
  `text_muted`; Up and Down in the field skip them, Return opens the current row once.
- Rows are plain list items: one line, the title first, " · " between parts, elided at the
  right. Mail and AI text never goes into a tooltip.
- The palette closes before it emits a choice, so the window sees no open dialog when it
  acts. Typing waits 200 ms before a search; the newest search's results win.

## Run page (`ui/run_view.py`, `ui/main_window.py`)

- Step 1, `review_panel`: "Step 1 of 2" (11px muted) over "Choose what MailBrief reads"
  (20px Semibold), the hint (a secondary `WrapLabel`), the shortlist and Continue, a
  `variant="primary"` button left-aligned. The run column's margins are 24, 20, 24, 20. Step 2, `consent_panel`: "Step 2 of 2" over
  "Approve sending to Groq", the disclosure (a `WrapLabel`) in a `HairlineFrame` card,
  then outline Approve and Decline side by side, Decline focused.
- The shortlist stays a `QListWidget`: each item keeps its text (what is read aloud), its
  ID under `UserRole`, flags, check state and app-text tooltip. `review()` adds a
  `ShortlistRow(subject, name, address, chips, blocked)` under `ROW_ROLE`, and
  `ShortlistDelegate` paints it with the brief list's row helpers: the rounded selection
  fill, focus ring only after keyboard focus, subject 13px Medium, sender 13px secondary
  (`sender_line`: the whole "Name <address>" when it fits; else the name cut at its end
  before " <address>"; else the address alone, cut by `cut_address`; a long display name
  never hides the address, and joined parts are re-measured, shrinking the cut part 1 px
  at a time, so the line never overflows). `cut_address` keeps the end of the domain,
  which says who sent it: the whole address; else the part before the "@" cut at its end
  and the whole domain ("bill…@paypal.com.secure-login.evil.example"); else "…@" and the
  domain cut at its start ("…@…login.evil.example"). Chips ("Tracked reply" and
  "Left out earlier" accent, "Excluded in Settings"
  warning). A cosmetic hairline separates rows; the last row has none, since the list's
  frame closes it.
- Height: once `review()` fills the list, its maximum height is its rows' `sizeHintForRow`
  plus twice the frame width. The shortlist has stretch 1 in `review_panel`, which has
  stretch 1 in the run column, and a stretch after Continue takes the height the list
  can't use: a short list shows no empty box, a long one fills the page and scrolls, and
  the page itself never scrolls at 1100 × 720.
- No horizontal scroll bar (`ScrollBarAlwaysOff`), and rows have no width of their own,
  so list mode gives them the viewport's width and no hidden horizontal range exists.
  Scrolling is per pixel.
- Check box: MailBrief paints its own inside the style's `SE_ItemViewItemCheckIndicator`
  rect, so the inherited `editorEvent` still handles clicks and Space, with
  `paint_check_box` from `ui/theme/indicators.py`, as the theme style paints every check
  box. Unchecked: a radius-4 rounded square with a cosmetic 1px `border_strong` outline.
  Checked: `primary_fill` fill, `accent_border` outline and an antialiased `on_primary`
  check mark. `indicator_colors(checked, tokens)` returns (border, fill, mark), with
  `NONE` ("transparent") for an unchecked box's fill and mark.
- A blocked row is muted, has no check box and can never be checked. Its check rect is
  computed as if it had one (`HasCheckIndicator` on a copied option), so its text starts
  at the same `text_left(option, index)` as a checkable row's.
- Automatic runs never show the run page.

## Buttons, lists and tabs

- A dialog page whose rows can outgrow the dialog goes in a scroll area
  (`settings_view._scrolling`: resizable, no frame, no focus, no sideways scrolling,
  named `{page}Scroll` with the tab's title as its accessible name). A
  squeezed form overlaps its rows: a wrapped note's last line hides behind the next row.
  A form field that is a box layout goes in a plain `QWidget`, so the form gives a
  wrapped label in it the height its text needs.

- "Outline" in this spec names a button made by `outline_button()`, not its look: every
  button is drawn as a glass pill.
- Every `QPushButton` that isn't a `GlassButton` (a plain `outline_button()`, a
  `variant="primary"` button, Qt's own in message boxes and button boxes) is drawn by
  `ThemeStyle` as a glass pill, the same as Settings' Disconnect at rest. The
  stylesheet has no `QPushButton` rules except the sidebar's `[variant="nav"]` rows; never
  add any, or Qt draws the stylesheet's box instead of the pill.
  - `paint_button_pill` (`PE_PanelButtonCommand`) paints `paint_liquid_pill` (or
    `paint_pill` in the other schemes) inside the same room as a `GlassButton`
    (`_PILL_ROOM_X`, `_PILL_ROOM_Y` 3): `pill_hover` at full strength under the pointer,
    shrunk to 0.97 while pressed or checked, and at half strength while disabled. It
    neither grows nor springs; only a `GlassButton` animates.
  - `sizeFromContents(CT_PushButton)` gives it a `GlassButton`'s size: the label (and
    icon) plus `_PILL_PAD_X` (10, the same as `PAD_X`) and `_PILL_PAD_Y` (6), plus the
    room. `polish` sets its label to Medium, keeping any size set before.
  - `variant="primary"` is the solid `primary` pill with its label in `primary_text`
    (`drawControl(CE_PushButtonLabel)`). A default button gets no second frame
    (`PE_FrameDefaultButton` draws nothing).
  - Focus (`PE_FrameFocusRect`): a 2 px ring inside the pill, only after keyboard
    navigation, in `accent_fg` (`primary_text` on the primary), as a `GlassButton`'s.
  - A disabled pill's label is the palette's disabled `ButtonText` (`text_muted`); the
    primary's stays `primary_text`.
- `QListView` (unless named, as the brief, action and page lists are): `panel`, a 1px
  `hairline` border, radius `RADIUS`, padding 3px; a list set to `NoFrame` (Your day's)
  is transparent, with no border or padding. `QListWidget::item`: padding 5px 10px,
  no border, radius `RADIUS`; `:selected` (also `:!active`): `selection` background,
  `text`, with no accent bar.
- `QLineEdit`, `QPlainTextEdit`, `QTextEdit`: `panel`, `text`, a 1px `hairline` border,
  radius `FIELD_RADIUS`, padding 4px 8px, selected text `on_primary` on `primary_fill`;
  `:focus` border `accent_fg`; `:disabled` `text_muted`. Line edits inside combo boxes and
  spin boxes have none of that.
- `ThemeStyle` (Fusion behind a `QProxyStyle`) draws what the stylesheet can't without
  image files: combo boxes, date and time fields and spin boxes as the same rounded field
  (`paint_field`, exactly as tall as a text field on every platform: the line height plus 12 px; text 8 px in) with thin `text_secondary`
  chevrons (`paint_chevron`), and check boxes and radio buttons (`paint_check_box`,
  `paint_radio`: `primary_fill` with an `on_primary` mark or dot when on). Sub-control
  values overlap between controls (a combo box's edit field is a spin box's down
  button), so its checks pair the control with the part.
- Pop-ups are windows of their own, so `ThemeStyle.polish` makes menus, combo box lists
  (`QComboBoxPrivateContainer`) and tooltips translucent, and their corners are clear,
  not square. The stylesheet rounds `QMenu` (radius `RADIUS`, padding 4px, items radius
  `MENU_ITEM_RADIUS` with a `selection` fill) and `QToolTip`. Its rules never reach a combo
  box's private list window, so `_RoundedPopup`, an event filter, paints that window as a
  rounded `panel` with a hairline edge in the theme in effect. A combo box's own delegate
  ignores the stylesheet's item rules, so `polish` gives it a `QStyledItemDelegate`; its
  list then has 4px padding and rounded `selection` rows.
- `QListView#actionList` and `QScrollArea#actionDetail` share the brief list's and detail's
  panel background, no border and no outline.
- `QTabWidget::pane`: no border. `QTabBar::tab`: pill segments: transparent,
  `text_secondary`, padding 5px 12px, margin 2px, radius `TAB_RADIUS`; `:hover` `text`;
  `:selected`: `text` on `selection`, with no underline.
- Briefs: the missed-days list hides when there are none; its note says why. An automatic
  refresh waits only while a replacement is being confirmed there, and reloads the page
  when it ends while Briefs shows.

## Dialogs (`ui/glass/dialog.py`)

Every dialog keeps the home screen's style: Plan with AI, New event, the draft and note
editor, Settings, the project dialog, message boxes and Qt's own dialogs alike.

- `ThemeStyle.polish` installs `dialog_sheet()`, one shared `DialogSheet` event filter, on
  every `QDialog`. On each paint, before the dialog's content paints over it, it paints:
  - the window's glass (`paint_panel` with the glass preferences, so the solid modes are
    solid) over a backdrop of its own, rendered at once at `SCALE` of the dialog's size
    with the colour field, and kept by size, scheme and mode (`CACHE_SIZE` 4);
  - a rounded `panel` sheet `DIALOG_GUTTER` (8) in from each edge, radius
    `SHEET_RADIUS` (12), with a hairline edge, both snapped to device pixels
    (`crisp_fill_rect`, `crisp_rect`).
- `ThemeStyle.pixelMetric` gives a dialog's own top-level layout `DIALOG_MARGIN` (22) on
  every side (`PM_Layout*Margin` for a `QDialog`), so its content sits 14 px inside the
  sheet, clear of its rounded corners. Keep a dialog's content in that layout and leave
  its margins alone; nested layouts and pages may set their own.
- Never paint a dialog's background, give it a stylesheet background or make it
  translucent: the sheet is its background. Text fields, lists and cards in a dialog sit
  on the white sheet as they do on a page.
- `tests/ui/theme/test_controls.py` checks that a plain dialog and a message box show the
  glass at the edge, no square corner and the sheet's straight edge, and that a dialog's
  layout gets `DIALOG_MARGIN`. `docs/ui/studio-dialog.png` is the reference render.

## Stylesheet additions from the Phase 4 visual pass

- Pills pad `PAD_X` 10 each side (`_PILL_PAD_X` for `ThemeStyle`'s), with 14px icons:
  14 cost the rounded sheet's room at 680 px, and 12 fails the 680 px width tests
  (`test_unbroken_mail_text_never_widens_the_workspace`,
  `test_a_long_unbroken_title_never_widens_the_page`) now that every button is a pill
  with room round it.
- Scrollbars are thin token-coloured handles with no arrow buttons: 8px wide, handle
  `border_strong`, radius 3, transparent track.
- Focus rings in the painted lists show only after the keyboard moves focus
  (`State_KeyboardFocusChange`), not on first show.
- Suggestion cards: 4px between title and meta, 8px before each button row.
- Long mail-derived button text ("Add to …", a proposal's effect) goes through
  `short_button_label` (40 characters, cut at a grapheme boundary, then "…"), with the full text as the button's
  accessible name; each "Add to" button has its own row.
- Settings is a `_NavButton` painted like a page row (icon at `_ICON_X`, label at
  `_LABEL_X`), so it lines up with the pages.
- `register_fonts()` returns False instead of raising; `apply_theme` then keeps Qt's font
  and still applies the colours. `apply_theme` is all or nothing. On failure it puts back
  the stylesheet, style, palette and font it found, sets tokens from the restored palette
  (`palette_tokens`) and raises; `main()` logs the failure and runs unthemed.

## Tests

- Never assert a top-level window's requested size. CI's macOS screen is small (about
  1024 × 649 available) and Cocoa keeps windows inside it, so a window asked for
  1100 × 720 or 1600 × 900 gets less there; offscreen gets whatever it asks for. Derive
  expectations from the actual geometry or from the screen's available area
  (`QGuiApplication.primaryScreen().availableGeometry()`), and `pytest.skip` a behaviour
  that needs more room than the screen has. Never call `widget.screen()` in a test: PySide
  re-parents the shared QScreen wrapper under that widget, and deleting the widget
  invalidates it.
