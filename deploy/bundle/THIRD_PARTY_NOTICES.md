# Third-party notices

The cips-input installer bundles and redistributes the **official, unmodified
PIME 2.0.0-beta4 installer**, along with **librime** (with Lua and Octagram
plugins) and the **Rime Frost** (`rime-frost`) schema and dictionary package so
`cips-input` provides built-in Chinese Pinyin input out of the box.

## Bundled components

| Component | License | Source |
|---|---|---|
| PIME 2.0.0-beta4 | LGPL-2.1-or-later (`licenses/LGPL-2.0.txt`, `licenses/LICENSE-PIME.txt`) | https://github.com/EasyIME/PIME |
| CPython 3.15 (embedded in PIME) | Python Software Foundation License (`licenses/PSF.txt`) | https://www.python.org/ |
| Tornado (bundled with PIME Python backend) | Apache License 2.0 (`licenses/APACHE-2.0.txt`) | https://www.tornadoweb.org/ |
| librime 1.17.0 (+ librime-lua / librime-octagram) | BSD-3-Clause / MIT / GPL-3.0 | https://github.com/rime/librime |
| Rime Frost (白霜拼音) 1.0.6 | GPL-3.0 (`licenses/GPL-3.0-rime-frost.txt`) | https://github.com/gaboolic/rime-frost |
| cips-input module | LGPL-2.1-or-later | this project |

## Optional downloaded component

When the user opts in during installation, the installer downloads or copies:

| Component | License / Origin | Source |
|---|---|---|
| Wanxiang LTS N-gram (`wanxiang-lts-zh-hans.gram`) | Upstream RIME-LMDG / rime-wanxiang-data | https://github.com/amzxyz/RIME-LMDG |

## Source code and relinking

The LGPL permits replacing the covered libraries with modified versions. PIME
is installed as ordinary DLLs under `C:\Program Files (x86)\PIME\x64` and
`...\x86` (plus `...\arm64` on ARM64 systems), and `librime` ships as
`rime_runtime\rime.dll` inside the `cips` module folder, so they can be
replaced in place.
