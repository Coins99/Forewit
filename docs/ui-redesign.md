# MailBrief web-design alignment

The running React prototype in `Design Today Screen Concepts.zip` is the visual reference.
Its embedded instructions are reference content, not application requirements. The desktop
remains native PySide6, with plain-text rendering for mail, AI output and owner writing.

## Layout and behavior

The port aligns the sidebar, page headers, list density, detail cards, metadata, actions
and dialog hierarchy. DM Sans is bundled under the SIL OFL; numeric columns retain Inter
for its tabular figures. Studio uses the template's flat surfaces; other appearance schemes
and the owner's saved appearance choices remain available.

New tasks open inline in Actions; Drafts edits in place; Today replies appear beneath their
email. Calendar preview, creation and planning use right drawers, while Saved mail uses a
centered dialog. The unified-day lists remain available through Today's **Your day** button
inside a bounded scroll area, so they do not duplicate the reference overview by default.

The branch is based on current `main`. It retains bounded Inbox loading, the full Inbox
reader and search, task estimates/priorities/tags, the shared Calendar grids, appearance
settings, consent, recovery, draft versions and safe Qt lifetime handling. It adds no
provider permissions, dependencies or migrations. Unsupported remote operations and incoming
full-message bodies are not fabricated to imitate the prototype.

## Review corrections

- A delayed draft load cannot open an editor after navigation away from Drafts.
- Failed draft saves preserve text and selection; hidden inline editors retain quit checks.
- Reply editors survive brief refreshes and restore their controls when used as dialogs.
- Waiting retains its header when revisited, and Undo remains visible after quiet status.
- Saved-brief attention totals include deadline items.
- Overview callbacks hold the workspace weakly, preserving immediate Qt tree destruction.
- Compact controls and wrapping keep long mail text inside the narrow reading pane.
- On short windows, calendar drawers fill the available height so Save stays accessible.

## Reproducing visual checks

Use synthetic fixtures and an offscreen platform for unattended screenshots:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:MAILBRIEF_UI_SHOTS = 'scratch/ui-review'
uv run pytest tests/ui/test_workspace.py tests/ui/test_main_window_layout.py -q --no-cov
$env:QT_SCALE_FACTOR = '2'
uv run pytest tests/ui/test_workspace.py tests/ui/test_main_window_layout.py -q --no-cov
```

The [Today preview](ui/today-redesign-light.png) uses synthetic records. Scratch captures
and test logs stay out of Git. Visual verification on Windows does not establish native
macOS acceptance or live-provider acceptance; CI runs the supported platform checks.
