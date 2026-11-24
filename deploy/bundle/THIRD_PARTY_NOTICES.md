# Third-party notices

The cips-input installer bundles and redistributes the **official, unmodified
PIME 2.0.0-beta4 installer**. Running it installs the PIME runtime, which
contains the components below. The cips-input module is added on top of that
runtime.

Nothing in this bundle is a modified copy of a third-party component: the
official installer is shipped byte-for-byte (SHA-256 is embedded in
`bundle.json` and verified before it runs), and the cips-input module is this
project's own code.

## Bundled components

| Component | License | Source |
|---|---|---|
| PIME 2.0.0-beta4 | LGPL-2.1-or-later (license text: `licenses/LGPL-2.0.txt`, `licenses/LICENSE-PIME.txt`) | https://github.com/EasyIME/PIME |
| CPython 3.15 (embedded in PIME) | Python Software Foundation License (license text: `licenses/PSF.txt`) | https://www.python.org/ |
| Tornado (bundled with the PIME Python backend) | Apache License 2.0 (license text: `licenses/APACHE-2.0.txt`) | https://www.tornadoweb.org/ |
| cips-input module | LGPL-2.1-or-later | this project |

PIME's installer may also install additional PIME input methods (Chewing,
Rime, cj/cin-based methods, node.js based methods). Those ship under their own
licenses, which the official PIME installer presents and installs; this
notice covers only what the cips-input bundle adds and triggers.

## Source code and relinking

The LGPL permits replacing the covered libraries with modified versions. PIME
is installed as ordinary DLLs under `C:\Program Files (x86)\PIME\x64` and
`...\x86` (plus `...\arm64` on ARM64 systems), so they can be replaced in
place. The corresponding source code is available at the PIME repository
linked above; the cips-input module's source and the scripts that build this
installer are in this project.

## The installer bootstrapper

`cips-input-setup-*.exe` itself contains only:

* the official PIME installer (unmodified),
* the cips-input module source files,
* PowerShell install/uninstall scripts written for this project,
* this notice and the license texts listed above.

It links against the .NET Framework that ships with Windows; no third-party
code is embedded in the bootstrapper.
