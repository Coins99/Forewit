# Studio: MailBrief's default look

Studio follows the supplied Today web template. The desktop retains the owner's saved
colour scheme and light/dark/system mode choices; the other schemes remain available.
See [web-design alignment](../ui-redesign.md) for layout, workflow adaptations and visual
verification commands.

The default header and sidebar use flat surfaces, with a compact daily overview, shared
page headers and inline task/draft editors. MailBrief's existing native pill controls,
consent, recovery and additional workflows remain available. Reduced transparency,
increased contrast and reduced motion remain supported for the alternative glass schemes.

Fonts are bundled DM Sans, with Inter for tabular numeric columns. Light Studio uses
white panes, a pale grey-green sidebar, blue selection/primary actions and red deadlines.
Colors are defined only in `ui/theme/tokens.py`; every scheme is covered by contrast tests.

## Reference renders

The synthetic fixtures render the current 1100 by 720 desktop in
[light](studio-light.png) and [dark](studio-dark.png), and a native
[project dialog](studio-dialog.png). The [Today preview](today-redesign-light.png) is also
used in the README. Regenerate them with the UI screenshot checks after an approved
visual change; keep scratch galleries and test logs out of Git.
