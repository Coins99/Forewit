# Studio: MailBrief's default look

Studio is MailBrief's default colour scheme and the design language every scheme now
shares. It adapts the owner's Apple product-page style reference (a "white gallery":
white canvas, Studio Mist grey bands, one blue, Semibold display type, no shadows) to a
dense desktop mail app. The tokens live in `ui/theme/tokens.py`; the component rules are
in `.claude/skills/mailbrief-ui/references/spec.md`.

## Decisions

The owner chose these on 2026-10-13:

1. **Studio is the default, and MailBrief follows the system's light or dark mode.**
   Before this, MailBrief always applied its dark set. Settings > Appearance has a new
   Mode choice: Follow the system (the default), Light or Dark. The six earlier schemes
   stay as choices and take on the shared shapes and type below.
2. **Glass stays, made calm.** Studio's header and sidebar are glass over a flat Studio
   Mist backdrop: no gradient, streak, sheen or lit edge. Reduce transparency, Increase
   contrast and Reduce motion still apply.

## Glass, matched to apple.com

Measured on apple.com in light mode (October 2026):

- Its bars are frosted, `rgba(250,250,252,0.8)` with `saturate(180%) blur(20px)`, and
  have no border, shadow or gradient. Over a flat page that is a flat pale grey, which
  is what Studio's chrome is.
- Its floating controls are soft translucent grey, `rgba(210,210,215,0.64)`, with no
  rim; its selected local-nav item is a plain fill. Studio's selected page is that grey
  capsule (`raised`), with no rim (`raised_rim` alpha 0).
- Its secondary buttons are outlined pills with no fill; they fill on hover, and have no
  glow. Studio's pills first matched them; the owner then chose glass at rest for every
  button (see Buttons like Disconnect). They keep the soft hover fill (`pill_hover`) and
  no coloured light, and the spring Apple's apps give a pressed or hovered glass button
  stays.
- Its primary is solid #0071e3 with white text, as Studio's.
3. **Desktop type scale.** Body text stays at 13 px, as in macOS Mail, rather than the
   web page's 17 px. Titles are Inter Semibold, tracked tighter, as Apple sets display
   type.

## Colour

| Role | Light | Dark | Guide name |
|---|---|---|---|
| Panes (`panel`) | #ffffff | #1d1d1f | Gallery White / Ink |
| Header, sidebar, cards (`canvas`, glass, `card`) | #f5f5f7 | #161617, #2a2a2c | Studio Mist |
| Selected row (`selection`) | #ebebef | #2c2c2e | near Control Gray |
| Dividers (`hairline`) | #d6d6d6 | #3a3a3c | Hairline Silver |
| Check box rims, scroll bars (`border_strong`) | #86868b | #6e6e73 | Steel |
| Text | #1d1d1f | #f5f5f7 | Ink |
| Secondary text | #515154 | #a1a1a6 | |
| Muted text | #68686d | #98989d | |
| Links, focus, proposal labels (`accent_fg`) | #0066cc | #2997ff | Apple Blue |
| The primary button (`primary_fill`, `on_primary`) | #0071e3 on #ffffff | the same | Pricing Blue |
| Deadlines (`warning_fg`) | #b64400 | #ff9f0a | Launch Orange |

Where the guide and WCAG disagree, WCAG wins: the guide's Slate (#707070) and Steel
(#86868b) text fall below 4.5:1 on a selected row, so secondary and muted text are a
little darker. `tests/ui/theme/test_theme.py` checks every text colour on panes,
selected rows and cards, in every scheme and mode.

## Shape and type

- **Cards** are a `card` fill with an 18 px radius (`CARD_RADIUS`) and no border or
  shadow. The guide's 28 px suits a web page's tall cards; MailBrief's are shorter.
- **Buttons** are glass pills, every one like Disconnect in Settings (see Buttons like
  Disconnect). Each screen has at most one primary button, a solid `primary_fill` pill
  with `on_primary` text (Sync and review, Continue).
- **Fields** (text, combo box, date, time and number) are rounded 8 px boxes with a
  hairline that turns `accent_fg` with focus. Combo boxes and date and time fields have
  a thin chevron. Check boxes and radio buttons fill with `primary_fill` when on.
- **No sharp corners.** The pages sit on one rounded white sheet (12 px corners) that
  floats on the glass, with a gutter of glass at its right; header, sidebar, gutter and
  status strip are one surface, and no hairline meets another. Dialogs sit on the same
  rounded sheet (see Dialogs). Focus rings, menus, combo box lists and tooltips are
  rounded too.
- **Lists** select with a rounded fill inset from the row's edges, and no accent bar.
  Row separators line up with the text. Tabs are pill segments.
- **Status labels** (deadlines, "Proposes a new deadline", "2 new in thread") are bare
  coloured text, with no chip behind them, as the guide marks what's new.
- **Type:** Inter Regular 13 px for text, 14 px for an email's summary, Medium for row
  titles, Semibold 20 px for page and email titles (`TITLE_PX`), 17 px for the brief's
  day (`HEADING_PX`) and 15 px for the app's name. From 15 px up, letters are tracked
  tighter by Inter's dynamic metrics (−0.33 px at 20 px). Inter SemiBold 4.001 is bundled
  beside Regular and Medium under the same SIL OFL.

## Not taken from the guide

- The 80 px hero type, 90 px section gaps and full-width imagery: a mail app has no hero.
- A blue primary for every action: the guide keeps it for "compact conversion moments",
  so MailBrief keeps it for one action a screen.
- Dropping glass: the guide's page has none, but the owner's goal is Apple's own app
  design, which has it.

## Liquid Glass, after WWDC25

From "Meet Liquid Glass" and the Human Interface Guidelines' Materials page, and how
MailBrief draws each part with QPainter (`ui/glass/painter.py`):

- **A floating layer for navigation only.** Glass is for the controls and navigation
  that float above content, never for the content itself, and never glass on glass.
  MailBrief's glass is the header, sidebar, gutter and status bar; the pages sit on the
  white sheet. The selected page is a vibrant white fill, not a second layer of glass.
- **Light, as a Liquid Glass tab takes it.** Measured pixel by pixel on a glass tab in
  dark mode (toolbar 69, body 74 on a 0–255 lightness scale): light enters along the two
  long edges, so the rim is a crisp line along the top (125) and an almost equal one along
  the bottom (119); from each, the light falls steeply within a point (102, 83) and then
  faintly over about six, so the middle of the glass is darkest. Round the ends the light
  fades out and the outermost edge falls into shade (down to 40, darker than the
  toolbar), fading in by one radius. There is no shadow above or below it. Studio's dark
  pills follow those numbers (`RIM_LIGHT`, `LENS_LIGHT`, `END_SHADE`); light mode, where
  white light can't show on a white page, keeps the same shaded ends and adds a faint
  contact ring and a soft shadow below.
- **Glass at rest.** WWDC's controls lift up into glass when touched, and Studio's
  buttons in the content first did: outlines that turned to glass under the pointer. The
  owner chose glass at rest instead, so every button, in the chrome, the pages and the
  dialogs, is the same frosted, lit pill as Disconnect in Settings. Under the pointer it
  brightens, and a `GlassButton` swells on a spring, with the light starting under the
  pointer and spreading round the pill.
- **Accessibility.** Reduce transparency makes the glass solid, Increase contrast draws
  solid rims, and Reduce motion stops the springs and the glide.

## A fluid colour field, after monopo saigon

monopo saigon's style ("a monochrome editorial gallery floating on molten light") keeps
all colour in one atmospheric layer behind a black, white and grey interface. Studio's
glass floats over three soft pools of light, sage at the top left, amber at the right and
rose low down, blurred so they flow into one another; the frosted chrome lets them through
at about half strength, as Liquid Glass picks up colourful content nearby. The content
sheet never takes a hue. Motion glides rather than snaps: the sidebar's selection moves
to a new page on monopo's expo-out curve, cubic-bezier(0.19, 1, 0.22, 1), in 420 ms.

## Buttons like Disconnect

Every button looks like Disconnect (now in Settings' Account card), which the owner picked
as the model on 2026-10-13: a pill of Liquid Glass at rest, wherever it sits, with its label in Inter
Medium 13 px and 10 px of glass each side of it, lit as described above. The glass pills
in the header, the sidebar and Today (`GlassButton`) also swell and light under the
pointer; `ThemeStyle` draws every other button, Qt's own in message boxes included, as the
same pill, brighter under the pointer and a little smaller while pressed. The one primary
is solid blue with a white label.

Icons are plain: a button's icon is drawn in its label's colour, beside the label, with
no tile or square behind it. (For a while a glass pill carried its icon in a rounded
tile, as a browser tab carries its site icon; the owner preferred plain icons.)

## Dialogs

Every dialog keeps the home screen's style (`ui/glass/dialog.py`): the window's glass,
with the colour field showing through, round a rounded white sheet (12 px corners, 8 px of
glass from the edge, a hairline edge), and the dialog's content 22 px in, inside the
sheet. It applies to every dialog without any code in the dialog: Plan with AI, New event,
the draft and note editor, Settings, the project dialog and message boxes alike.

## Reference renders

`studio-light.png` and `studio-dark.png` are the window at 1100 × 720 in each mode, and
`studio-dialog.png` is the project dialog, rendered by the UI tests' gallery (see the
mailbrief-ui skill). Compare new work with them, and replace them when the owner approves
a change to the look.

## A dashboard, after Dub

Dub's style ("frosted link dashboard on rice paper") states a figure plainly and marks
state with small dots. "Your day" does the same: each card names what it counts and shows
the count as a 22 px Semibold figure (muted at 0), and a due row or a row with new replies
has a 6 px dot of its colour before its note.
