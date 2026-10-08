# Web UI — design & copy guide

Audience: business users (ops, finance, clinical/retail managers), not engineers. Every screen answers "what do I need to
decide or do here?" Technical details exist but are hidden unless the user turns on **Technical details** (user menu →
`usePrefs().advanced`, or wrap in `<Advanced>…</Advanced>` from `@/state/prefs`).

## Identity — "provenance"
What sets this platform apart from a plain RAG chatbot: every answer and every number traces back to its source, is
checked, and can be reviewed by a human. The visual language follows that idea:

| token | value | use |
|---|---|---|
| `ink-900` `#1E1B4B` | navigation rail, headings, primary dark button (`btn-ink`) |
| `paper` `#F6F5FB` | app background |
| `brand-600` `#5B4CF5` | the main action on a screen (`btn-primary`), links, focus |
| `marker-200` `#FDE68A` | **evidence only**: citations (`.cite-chip`), highlighted source text (`.marker`), "matches the source" emphasis |
| `verify-600` `#0E9F6E` | verified / matched / done |
| `alert-500/700` | errors, mismatch, destructive |

Marker yellow is the one bold element — never use it for decoration, buttons or headings.

Type: `font-display` (Bricolage Grotesque) for page titles, section titles, big numbers; body is Be Vietnam Pro (default
`font-sans`). Sentence case everywhere. No ALL-CAPS labels, no eyebrow labels above every heading, no `·`-joined meta
strings when a short phrase reads better, no `→` appended to button text.

Surfaces: `.panel` (r=20) for a page's main region, `.card` (r=14) for list items, `.well` for quiet grouping. Prefer lists
and whitespace over grids of identical cards. Status colours via `<StatusBadge>`; generic chips via `<Badge tone>`.
Icons: lucide-react, 16–18px, `text-ink-400/600`. File types: `<SourceIcon type>` (no emojis).

## i18n (required)
All user-visible strings go through `useT()` from `@/i18n` → `t("area.key", {vars})`. Each area owns a dictionary file
`src/i18n/<area>.ts` exporting `{ en, vi }` (keys prefixed by area, e.g. `sources.upload.title`). Add both languages for
every key. Text from the API in `{en, vi}` form → `pick()` from `useI18n()`. Dates/numbers: `fmtDate`, `fmtRelative`,
`fmtNumber`, `fmtPct` from `@/lib/format` (locale-aware). Status/role labels: `statusLabel`, `roleLabel`.

## Copy
Name things by what the user gets: "Sources", "Ask", "Explore data", "Knowledge map", "Trust center"; "records" →
"passages", "entities" → "people, places & things", "chunks/embeddings/jobs" only in Technical details. Buttons say exactly
what happens ("Upload files", "Build the view", "Approve and send"). Empty states invite the next action. Errors say what
happened and what to do.

## Motion
One orchestrated moment per screen at most (e.g. the provenance trail drawing in on Home). Respect reduced motion.
