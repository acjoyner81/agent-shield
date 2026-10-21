# Portal UI Design Guide

This guide records the current Angular portal design so new pages and features feel like part of AgentShield. Treat it as the default for new portal work. When the product needs a deliberate visual change, update this guide with the implementation.

## Visual direction

The portal uses a quiet enterprise control plane style. A dark navy navigation rail frames a light gray canvas, white content panels, and teal highlights. Use clear page titles, short supporting copy, compact labels, and restrained status colors. Prefer the existing components and tokens over one off styling.

## Shell and page structure

- Keep the shared shell in `portal-frontend/src/app/app.html` and `portal-frontend/src/app/app.css`.
- Use the shell’s dark left sidebar for the brand, workspace identity, grouped navigation, service status, and profile controls.
- Use the light top bar for breadcrumbs, shared service status, notifications, and Help Center access.
- Put route content inside the shell’s `.content-wrap`. The current maximum width is 1440px, centered with generous desktop padding.
- A page should start with a `.page-head` section. Put an uppercase category label in `.eyebrow`, a single `h1`, and an optional one sentence `.subhead` in the text column. Put the page’s primary action in the action column when one exists.
- Follow the heading with a small number of focused panels or grids. Use `.panel`, `.metric-card`, and existing layout classes where they match the content.
- Use links for navigation and buttons for actions. A visual affordance must perform the action it implies. Do not render a caret, ellipsis, or button styling for a noninteractive element.

## Type and color

The main tokens are defined in `portal-frontend/src/app/app.css`:

| Token | Current value | Use |
|---|---|---|
| `--ink` | `#10212b` | Main text and shell navy |
| `--muted` | `#73818b` | Supporting text |
| `--line` | `#dce4e8` | Panel and divider borders |
| `--canvas` | `#f4f7f8` | Main page background |
| `--panel` | `#fff` | Cards and panels |
| `--navy` | `#10212b` | Sidebar background |
| `--teal` | `#1db89a` | Primary action and healthy state |
| `--yellow` | `#eab84a` | Caution state |
| `--coral` | `#e46b62` | Error or degraded state |
| `--violet` | `#7968d8` | Secondary identity accent |

Use Manrope for interface text and DM Mono for compact technical labels, timestamps, and status values. The shell loads these fonts. Do not add a separate font import to a page.

Keep the hierarchy consistent:

- Page titles use `h1`, about 30px on desktop, with tight line height and negative tracking.
- Panel titles use `h2`, about 15px.
- Eyebrows and compact metadata use DM Mono, uppercase, with letter spacing.
- Supporting descriptions use muted text and comfortable line height.
- Keep status meaning in text as well as color. Teal, yellow, and coral alone are not enough to communicate health.

## Spacing and components

- Use the existing 8px corner radius for panels, 5px for buttons, and 1px borders in `--line`.
- Use `.button`, `.button-primary`, and `.button-secondary` for actions. Keep a single clear primary action for a page or dialog.
- Use `.panel-title` to align a panel heading with its secondary action or status.
- Use `.text-link` for secondary navigation inside content.
- Keep dense event tables inside `.table-wrap` so they can scroll horizontally without widening the whole page.
- Use `.stale-note` when previously loaded information remains visible after a refresh failure. State that the data is old; do not imply it is current.
- Reuse the shared notification and profile menus in the shell. Feature pages should not create duplicate global controls.
- Prefer shared classes and small component styles over long inline style attributes. Put shell wide styling in `app.css`; put feature specific styling beside the feature.

## Responsive behavior

The current shell and dashboard use two breakpoints in `app.css`:

- At widths up to 1000px, reduce sidebar and content spacing, collapse the dashboard panels to one column, and reduce wide card grids to two columns.
- At widths up to 700px, hide the desktop sidebar, use the compact top bar, reduce page padding, stack page headings and actions, and collapse card grids to one column.
- Preserve readable text and usable controls at narrow widths. Use `min-width: 0` on flexible content, allow long values to wrap, and put genuinely wide tables in a local scroll container.
- Dialogs and menus must fit the viewport, keep their actions reachable, and avoid forcing the entire page to scroll sideways.
- Check keyboard focus and visible focus states at every breakpoint.

## Interaction and accessibility

- Use semantic landmarks and elements: `header`, `nav`, `main`, `section`, headings in order, anchors for route changes, and buttons for state changes.
- Give icon only controls an accessible name. Reflect expandable state with `aria-expanded` and associate the trigger with the controlled panel.
- Make menus and dialogs operable with a keyboard and provide a visible `:focus-visible` state.
- Use `role="status"` or a live region for non urgent outcomes such as a completed refresh. Use an alert only for an issue that requires immediate attention.
- Disable a control only while its action is unavailable, and show a clear busy or result state when an action runs.
- Do not fake controls with decorative glyphs. If an element looks interactive, wire it up; otherwise remove the cue or present it as plain text.

## Page headers and examples

Standard operational pages should follow this pattern:

```html
<section class="page-head">
  <div>
    <p class="eyebrow">Workspace / section</p>
    <h1>Page title</h1>
    <p class="subhead">A short description of what the user can do here.</p>
  </div>
  <div class="head-actions">
    <button class="button button-secondary" type="button">Secondary action</button>
    <button class="button button-primary" type="button">Primary action</button>
  </div>
</section>
```

For a page without actions, omit `.head-actions`. At mobile widths, the heading and actions stack. Dialogs should use a concise title, one clear task, labeled fields, and explicit Cancel and submit actions.

## Service status and labels

Health labels must come from the service health data, not a hardcoded “All systems operational” claim. In the dashboard, use user friendly names for internal service identifiers. Distinguish the Python LLM gateway from the Java edge gateway when both are shown. Present the exact status and a short detail when degraded, and keep the status indicator consistent with that text.

## Source of truth

The current implementation lives in:

- `portal-frontend/src/app/app.html` and `portal-frontend/src/app/app.css` for the shell, global layout, tokens, and responsive rules.
- `portal-frontend/src/app/features/dashboard/dashboard.component.ts` for page header, card, panel, and service health examples.
- `portal-frontend/src/app/features/keys/keys.component.ts` for forms, credentials, and developer guidance patterns.
- `portal-frontend/src/app/features/logs/logs.component.ts` for filters and dense tabular content.

When a convention in this guide and the live portal differ, inspect the live component and update this guide if the change is intentional. Avoid treating temporary mock content or inactive visual controls as established behavior.
