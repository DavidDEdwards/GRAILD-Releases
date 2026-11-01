# Bundled UI assets

`tailwind.css` is generated with the official Tailwind CSS v3.4.17 standalone Windows x64 CLI from https://github.com/tailwindlabs/tailwindcss/releases/tag/v3.4.17 . Its MIT license is included.

To regenerate after adding UI classes, run the downloaded build-only CLI (or the equivalent Tailwind 3.4.17 CLI) from the project root:

```powershell
& '.\build\standalone-audit\tailwindcss-windows-x64.exe' -i tailwind.input.css -o assets/tailwind.css --minify
```

The content configuration scans index.html, canary_ui.js, and update_ui.js, including full class names assigned by JavaScript. Keep new conditional classes as complete strings so the compiler detects them. The generated CSS is checked into source; neither Node nor this CLI is needed to run or package the app.

Inter 400, 600, 700, and 900 are downloaded from Google Fonts' Inter distribution (v20). `fonts.css` references only local files. Their SIL Open Font License is included as Inter-OFL.txt. No CDN is contacted for interface resources.

Artwork remains dynamic: user-imported album covers are external user data; Canary artwork is downloaded when requested and cached locally. App icons and decorative symbols in the HTML are Unicode emoji, rendered using Windows' fonts. There are no missing project-only image/icon files.
