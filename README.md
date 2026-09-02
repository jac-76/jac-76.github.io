# jac-76.github.io

Personal site. Runs on GitHub Pages from the `main` branch root.

## Editing
- `index.html` — single-page site (projects grid is hydrated from JS).
- Project cards live in the `projects` array at the bottom of `index.html`.
- Dark theme, system fonts + JetBrains Mono for code. Match existing style.

## Blog posts
- Source: `blog/<slug>.md` (YAML front matter + Markdown).
- Render to a standalone styled page with the stdlib-only renderer:
  `python3 render_post.py blog/<slug>.md blog/<slug>.html`
- Link it from the `# flagship_writeup` section (or a posts list) in `index.html`.

## Publish
Push to `main`. GitHub Actions builds it automatically via Pages (Deploy from branch).