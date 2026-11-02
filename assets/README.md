# Bundled UI assets

`tailwind.css` is generated with the official Tailwind CSS v3.4.17 standalone Windows x64 CLI from https://github.com/tailwindlabs/tailwindcss/releases/tag/v3.4.17 . Its MIT license is included.

To regenerate after adding UI classes, run the downloaded build-only CLI (or the equivalent Tailwind 3.4.17 CLI) from the project root:

```powershell
& '.\build\standalone-audit\tailwindcss-windows-x64.exe' -i tailwind.input.css -o assets/tailwind.css --minify
```

The content configuration scans index.html, canary_ui.js, and update_ui.js, including full class names assigned by JavaScript. Keep new conditional classes as complete strings so the compiler detects them. The generated CSS is checked into source; neither Node nor this CLI is needed to run or package the app.

Inter 400, 600, 700, and 900 are downloaded from Google Fonts' Inter distribution (v20). `fonts.css` references only local files. Their SIL Open Font License is included as Inter-OFL.txt. No CDN is contacted for interface resources.

Artwork remains dynamic: user-imported album covers are external user data; Canary artwork is downloaded when requested and cached locally. Decorative symbols use system emoji fonts. App branding uses the bundled PNG, ICO, and ICNS files described below.

## App logo

The graild-logo.png, graild.ico and graild.icns assets are format exports of the publisher-supplied Crowned Purple Wave G Logo. The original is preserved at branding/logo-source.png. Regenerate with branding/generate_icons.py and Pillow in a build environment. Pillow is not an app runtime dependency. The supplied design and background are retained.
