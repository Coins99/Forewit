# Studio: Forewit's default look

Studio follows the supplied Today web template. The desktop retains the owner's saved
colour scheme and light/dark/system mode choices; the other schemes remain available.
See [web-design alignment](../ui-redesign.md) for layout, workflow adaptations and visual
verification commands.

The default header and sidebar use flat surfaces, with a compact daily overview, shared
page headers and inline task/draft editors. Consent, recovery and additional workflows
remain available. Reduced transparency, increased contrast and reduced motion remain
supported for the alternative glass schemes.

## Buttons and date fields

Every button is flat, in every colour scheme, to match the template's fields and
switchers (the owner's choice, 2026-11-06; before then buttons were Liquid Glass pills):

- An 8 px rounded rectangle, the same as a text field: white (`panel`) in a hairline edge
  at rest, grey (`card`) under the pointer, a little darker pressed, and the selection
  colour with the accent edge while a toggle is on. Nothing grows or animates.
- The one primary button of a screen is solid `primary_fill` (Studio light's blue,
  `#2f62d4`, the same blue as the Calendar's today and the selected rows) with a white
  label; it darkens a little under the pointer.
- Labels are 13 px Medium. Dense rows (the brief's suggestion cards, the Tasks toolbar's
  More) use compact 9 px buttons.
- A keyboard focus ring (2 px, the accent) shows after Tab, never after a click.
- With the system's Increase contrast, every button edge is the darker contrast rim.

Every date field's drop-down calendar is the same flat style: a rounded panel, chevron
arrows, plain weekday names (no red weekends), the chosen day a rounded blue cell and
today ringed in the accent.

Fonts are bundled DM Sans, with Inter for tabular numeric columns. Light Studio uses
white panes, a pale grey-green sidebar, blue selection/primary actions and red deadlines.
Colors are defined only in `ui/theme/tokens.py`; every scheme is covered by contrast tests.

## Reference renders

The synthetic fixtures render the current 1100 by 720 desktop in
[light](studio-light.png) and [dark](studio-dark.png), and a native
[project dialog](studio-dialog.png). The [Today preview](today-redesign-light.png) is also
used in the README. Regenerate them with the UI screenshot checks after an approved
visual change; keep scratch galleries and test logs out of Git.

Current application screenshots use Forewit. Older design mockups linked from the
workspace specification are historical references and may retain the MailBrief name.
