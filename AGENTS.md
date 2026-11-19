# Verification

- Run the Python regression suite with `python -m pytest -q`.
- Run renderer smoke checks with `node tests/render_smoke.js`.
- Run theme registry and storage synchronization checks with `node tests/theme_smoke.js`.
- Check JavaScript syntax with `node --check <changed-file>`.
- Run `git diff --check` before committing.

# Frontend conventions

The frontend uses vanilla JavaScript IIFE modules and script tags without a build step. `window.UITheme.registry` is the theme metadata source; retain the `THEMES` and `LABELS` compatibility exports. `nova:themechange` is dispatched on window when the active theme changes. Storage events update the current tab without writing the preference back.
