# Liquid Glass

MailBrief's navigation chrome is Liquid Glass, painted with QPainter. The header, the
sidebar and the pill buttons are glass. The lists and the reading pane stay opaque, so
mail is never read through glass. The planner's prototype renders are the visual
reference; `docs/ui/mockup-three-pane-dark.png` stays the layout reference.

The component rules, tokens and APIs are in the UI spec
(`.claude/skills/mailbrief-ui/references/spec.md`, "Glass tokens" and "Glass painting").
This page says what was built, why, and what it costs.

## How it's drawn

- **Backdrop:** the glass shows a backdrop painted from tokens: a diagonal gradient under
  soft streaks of light. No live content is ever shown through glass, so text contrast
  can be proven.
  - Only the blurred backdrop is ever seen. It is painted at a quarter of the window's
    size, blurred there, and scaled up by the glass.
  - It renders on a worker thread and returns by a queued Qt signal. The last four
    sizes are cached.
- **Each panel:** an opaque underlay, the blurred backdrop, a tint, a sheen at the top
  and a lit top edge.
- **The selected page:** a raised fill on the glass, never a second layer of glass.
- **Pill buttons:** a fill that is lighter on top and shaded below, with a rim one
  device pixel wide.
  - Rims snap to the device pixel grid, so they stay crisp at 125% and 150%.
  - Hovering animates in, as Apple's glass lights from within on interaction. The pill
    grows a little on a spring, a glow starts inside its edge under the pointer and
    spreads round it, and its rim lights near the pointer. Leaving eases it back.
  - Only a 3 px band inside the edge and the rim light up: labels sit 6 px in, and any
    light on the body would cut the dark primary pill's text below 4.5:1.
  - Pressing shrinks a pill, and it springs back on release.

## Colour and contrast

- **Schemes:** six, each with a dark and a light set: Slate (grey-blue, the default),
  Graphite, Ocean, Sage, Plum and Rose. The owner picks one in Settings > Appearance.
- **Proof:** for every scheme and mode, `tests/ui/theme/test_glass.py` composites each
  glass surface over the backdrop's darkest and brightest pixels, under full sheen or
  shade. Text on glass must reach 4.5:1, and the focus ring 3:1 beside it.
- **Real pixels:** `tests/ui/glass/` checks the painted pixels too.
- **Muted text:** `text_muted` fails on glass, so it never sits there. Muted labels in
  the sidebar show as `text_secondary`.

## Accessibility

| Setting | Glass |
|---|---|
| Reduce transparency (macOS, or Windows "Transparency effects" off) | Solid, no backdrop or sheen |
| Increase contrast (macOS, or Windows high contrast) | Solid, with solid rims in `rim_contrast` |
| Reduce motion (macOS, or Windows "Animation effects" off) | No lift, press or spring |

- **When it's read:** at start, whenever MailBrief becomes the active app and when Qt
  reports a contrast change. A failed read counts as no preference.
- **The owner's choices:** Settings > Appearance can make glass "Always solid" or
  "Reduce motion" whatever the system says. Neither can undo a system setting.

## Cost

Paint times, measured with paint profiling on an Apple silicon laptop (2026-10-13). Each
size had 50 repaints of the whole workspace and 50 of a hovered pill; times are p50 /
p95 in milliseconds.

| Widget | 1100×720, 1x | 1600×1000, 1x | 1100×720, 2x | 1600×1000, 2x |
|---|---|---|---|---|
| Header | 0.04 / 0.21 | 0.07 / 0.32 | 0.06 / 0.57 | 0.09 / 0.93 |
| Sidebar | 0.19 / 0.22 | 0.25 / 0.60 | 0.44 / 0.46 | 0.72 / 1.12 |
| Pill button (at every point of the hover animation) | 0.18 / 0.20 | 0.18 / 0.19 | 0.26 / 0.29 | 0.26 / 0.29 |
| Sidebar page row | 0.02 / 0.08 | 0.02 / 0.08 | 0.03 / 0.11 | 0.03 / 0.12 |
| Sidebar button | 0.02 / 0.02 | 0.02 / 0.03 | 0.02 / 0.03 | 0.02 / 0.03 |

- **Paint budget:** the plan allowed 8 ms at p95 for animated widgets, and the slowest
  measured is 1.12 ms. The pill row was measured again with the hover animation (glow
  and growth), and the other rows didn't change.
- **Backdrop:** rendering takes 0.4 ms at 1100×720 and 1.8 ms at 3840×2160 (p50), on the
  worker thread. A 4K window's blurred backdrop is 2 MB.
- **Package:** the macOS app grew by 12 KB, with the same Qt libraries and files. The
  glass uses no new dependencies or Qt modules.

### Measuring on your machine

```bash
MAILBRIEF_PAINT_PROFILE=1 /Applications/MailBrief.app/Contents/MacOS/MailBrief
```

Use MailBrief, then quit. `desktop.log` in MailBrief's data folder then has one line per
widget kind, such as `paint widget=GlassButton count=120 p50_ms=0.13 p95_ms=0.20`. It
records nothing else. `tests/ui/glass/test_profile.py` fails if any glass paint's p95
passes 50 ms, a margin for slow CI machines that still catches a regression such as
blurring on every paint.

## Not built

- **The time slider** from the plan: the owner kept the 15-minute dropdown (#58).
- **A segmented control:** MailBrief has no control that needs one yet.
- **Refraction, chromatic edges and lenses:** these need per-pixel work that Python and
  QPainter can't do at frame rate, or Qt Quick shaders, which would add Qt modules.
- **Native materials:** Windows Mica or Acrylic, and macOS `NSGlassEffectView`, would
  put the system wallpaper behind the glass, which breaks the contrast proof, and they
  need platform-specific window handling.

## A Qt rule the glass work turned up

`QApplication::setStyle` copies its list of every widget, then visits each one, sending
events that run Python code (the theme followers that re-tint icons). If Python's garbage
collector runs then, it can free a window nothing else references, and Qt crashes on its
freed widgets. Wrap any work that makes Qt walk every widget in `paused_collection()`.
`apply_theme` and the glass repaint already do.

## Screenshots

```bash
MAILBRIEF_UI_SHOTS=scratch/ui-shots uv run pytest tests/ui/glass/test_gallery.py tests/ui/test_workspace.py -q --no-cov
QT_SCALE_FACTOR=2 MAILBRIEF_UI_SHOTS=scratch/ui-shots uv run pytest tests/ui/glass/test_gallery.py tests/ui/test_workspace.py -q --no-cov
```

The gallery saves:

- `glass-{scheme}-{mode}-{dpr}x.png`: a glass sampler for every scheme.
- `glass-states-{theme}-{dpr}x.png`: every pill state on the glass header.
- `accessibility-{mode}-{dpr}x.png`: the workspace in each glass mode.
- The workspace pages, as before.
