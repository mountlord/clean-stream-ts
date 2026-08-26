# Packaging vendor files

These are NOT committed to the repo - fetch them once, after which the
packager runs offline.

```
packaging/windows/vendor/
├── 7zSD.sfx          self-extractor module
├── 7zr.exe           standalone 7-Zip compressor
└── ffmpeg/
    ├── ffmpeg.exe
    ├── ffprobe.exe
    └── *.dll         (shared builds only)
```

## 7zSD.sfx and 7zr.exe

Both come from the **LZMA SDK**, not from the 7-Zip "extra" package - modern
extra packages no longer carry SFX modules.

1. https://7-zip.org/sdk.html
2. Download the LZMA SDK archive (e.g. `lzma2602.7z`)
3. Copy `bin\7zSD.sfx` and `bin\7zr.exe` into this folder

## ffmpeg

Use an **LGPL** build. CleanStreamTS never encodes video - it stream-copies
(`-c copy`) and decodes only to validate - so the GPL encoders (`libx264`,
`libx265`) are not needed. An LGPL build is smaller and carries no GPL
redistribution obligations.

Recommended: `ffmpeg-master-latest-win64-lgpl-shared` from
https://github.com/BtbN/FFmpeg-Builds/releases

Copy `bin\ffmpeg.exe`, `bin\ffprobe.exe` and the accompanying `bin\*.dll`
into `vendor/ffmpeg/`.

**Do not** substitute a GPL build (gyan.dev "essentials"/"full", or BtbN's
`-gpl-` variants). It would work, but it would make the installer a GPL
derivative and attach source-offer obligations to the release.

Ship the ffmpeg `COPYING.LGPLv2.1` text alongside the installer or quote it
in the release notes.
