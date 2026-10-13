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
  glow. Studio's glass pills are the same: a 1 px Steel rim, no fill, a soft fill
  (`pill_hover`) as the pointer lifts them, and no coloured light. They now match the
  stylesheet's outline buttons. The spring Apple's apps give a pressed or hovered glass
  button stays.
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
| Outline buttons (`border_strong`) | #86868b | #6e6e73 | Steel |
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
- **Buttons** are pills. Outline buttons have a 1 px `border_strong` rim and no fill.
  Each screen has at most one primary button, a solid `primary_fill` pill with
  `on_primary` text (Sync and review, Continue).
- **Fields** (text, combo box, date, time and number) are rounded 8 px boxes with a
  hairline that turns `accent_fg` with focus. Combo boxes and date and time fields have
  a thin chevron. Check boxes and radio buttons fill with `primary_fill` when on.
- **No sharp corners.** The pages sit on one rounded white sheet (12 px corners) that
  floats on the glass, with a gutter of glass at its right; header, sidebar, gutter and
  status strip are one surface, and no hairline meets another. Focus rings, menus, combo
  box lists and tooltips are rounded too.
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
- **Lensing and highlights.** Glass defines its shape by bending and concentrating light
  at its edges, and light sources make specular highlights that follow its geometry. A
  pill on the glass has a lensing band 3 px inside its edge (bright along the top,
  faintly shaded along the bottom) and a 1 px specular rim, bright along the top,
  brightest at the top left and reflected at the bottom right.
- **Shadows that ground it.** An even contact edge (Apple's `#e6e6e8` ring in light
  mode) and a soft shadow below, outside the pill only.
- **Lifting into glass.** Controls in the content rest quietly and lift up into glass
  when touched: Accept, Dismiss, Draft a reply and Open in Gmail rest as outlines and,
  under the pointer, gain the frosted body, highlight and shadow as they swell on a
  spring. The light still starts under the pointer and spreads round the pill.
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

## Buttons like tabs

A glass pill with an icon carries it as a browser tab carries its site icon: a rounded
square tile (18 px, radius 5) at its leading end, concentric with the pill's round end,
with the glyph in white on Steel grey (on the primary pill, a lighter blue tile), then the
label in Inter Medium. Light along the top rim and a softer bottom rim give it the same
glassy edge as the tab it is modelled on.

## A dashboard, after Dub

Dub's style ("frosted link dashboard on rice paper") states a figure plainly and marks
state with small dots. "Your day" does the same: each card names what it counts and shows
the count as a 22 px Semibold figure (muted at 0), and a due row or a row with new replies
has a 6 px dot of its colour before its note.
