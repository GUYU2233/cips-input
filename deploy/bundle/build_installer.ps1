# Builds the one-file cips-input installer.
#
# The output is a single .exe that contains:
#   * the official, unmodified PIME 2.0.0-beta4 installer (downloaded once and
#     verified against a pinned SHA-256),
#   * the cips-input module from src/input_methods/cips,
#   * embedded librime 1.17.0 (x86) + precompiled rime-frost 1.0.6 data
#     (staged into build\bundle\rime_stage via deploy\prepare_rime.ps1),
#   * install.ps1 / uninstall.ps1 / common.ps1,
#   * license texts and THIRD_PARTY_NOTICES.md,
#   * a small .NET bootstrapper compiled with the csc.exe that ships with
#     Windows (no Visual Studio, NSIS, 7-Zip or PyInstaller required).
#
# The large Wanxiang language model (wanxiang-lts-zh-hans.gram, ~373 MB) is
# intentionally NOT embedded into the .exe; the installer makes attaching it
# an optional choice at install time (online download or local file).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File deploy\bundle\build_installer.ps1
#   powershell -File deploy\bundle\build_installer.ps1 -PimeSetup D:\tmp\PIME-setup.exe
#   powershell -File deploy\bundle\build_installer.ps1 -SkipDownload -SkipSelfTest
#
# Output: build\dist\cips-input-setup-<version>.exe + SHA256SUMS.txt

[CmdletBinding()]
param(
    [string]$PimeSetup,
    [string]$OutDir,
    [switch]$SkipDownload,
    [switch]$SkipSelfTest,
    [switch]$KeepStage
)

$ErrorActionPreference = 'Stop'

$script:PimeSetupName = 'PIME-2.0.0-beta4-setup.exe'
$script:PimeSetupUrl = 'https://github.com/EasyIME/PIME/releases/download/v2.0.0-beta4/PIME-2.0.0-beta4-setup.exe'
$script:PimeSetupSha256 = '3bc146bd89daa6b5eb89d2346ece445f8752e0df6d194959644e359ea197fcdf'
$script:PimeSetupSize = 48664288

$script:GramFileName = 'wanxiang-lts-zh-hans.gram'
$script:GramUrl      = 'https://github.com/amzxyz/rime-wanxiang-data/releases/download/LTS/wanxiang-lts-zh-hans.gram'
$script:GramSha256   = 'e9ddd501b7d536bc5ea1346f8f900ae49a719a05be349e0a17cb27e717143ca5'
$script:GramSize     = 390786092

function Write-Step {
    param([string]$Message)
    Write-Host ''
    Write-Host ('==> ' + $Message) -ForegroundColor Cyan
}

function Write-Note {
    param([string]$Message)
    Write-Host ('    ' + $Message)
}

function Assert-File {
    param([string]$Path, [string]$What)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw ('{0} not found: {1}' -f $What, $Path)
    }
}

$bundleDir = $PSScriptRoot
$repoRoot = Split-Path -Parent (Split-Path -Parent $bundleDir)
$srcModule = Join-Path $repoRoot 'src\input_methods\cips'
$imeJson = Join-Path $srcModule 'ime.json'
Assert-File $imeJson 'ime.json'
$ime = Get-Content -LiteralPath $imeJson -Raw | ConvertFrom-Json
$version = [string]$ime.version
if (-not $version) { throw 'ime.json has no version field.' }

$pimeVersion = '2.0.0-beta4'
$pimeVersionFile = Join-Path $repoRoot '_pime\version.txt'
if (Test-Path -LiteralPath $pimeVersionFile) {
    $fromRepo = (Get-Content -LiteralPath $pimeVersionFile -Raw).Trim()
    if ($fromRepo) { $pimeVersion = $fromRepo }
}

$buildRoot = Join-Path $repoRoot 'build'
$cacheDir = Join-Path $buildRoot 'cache'
$workDir = Join-Path $buildRoot 'bundle'
$rimeStageDir = Join-Path $workDir 'rime_stage'
$payloadDir = Join-Path $workDir 'payload'
$stageDir = Join-Path $workDir 'installer'
$distDir = if ($OutDir) { [IO.Path]::GetFullPath($OutDir) } else { Join-Path $buildRoot 'dist' }
$exeName = ('cips-input-setup-{0}.exe' -f $version)

Write-Step 'cips-input installer build'
Write-Note ('repo:     ' + $repoRoot)
Write-Note ('version:  ' + $version)
Write-Note ('PIME:     ' + $pimeVersion)
Write-Note ('output:   ' + (Join-Path $distDir $exeName))

# ---------------------------------------------------------------------------
# toolchain
# ---------------------------------------------------------------------------

Write-Step 'Locating the .NET Framework compiler'
$cscCandidates = @(
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'),
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe')
)
$csc = $null
foreach ($candidate in $cscCandidates) {
    if (Test-Path -LiteralPath $candidate) { $csc = $candidate; break }
}
if (-not $csc) {
    throw 'csc.exe (.NET Framework 4.x) was not found. Install .NET Framework 4.8 and retry.'
}
Write-Note ('csc: ' + $csc)

# ---------------------------------------------------------------------------
# bundled PIME installer
# ---------------------------------------------------------------------------

Write-Step 'Resolving the bundled PIME installer'
$setupPath = $null
if ($PimeSetup) {
    $setupPath = [IO.Path]::GetFullPath($PimeSetup)
    Assert-File $setupPath 'PIME installer'
} else {
    $setupPath = Join-Path $cacheDir $script:PimeSetupName
    if (-not (Test-Path -LiteralPath $setupPath)) {
        if ($SkipDownload) {
            throw ('PIME installer is not cached ({0}) and -SkipDownload was given.' -f $setupPath)
        }
        New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null
        Write-Note ('downloading ' + $script:PimeSetupUrl)
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $script:PimeSetupUrl -OutFile $setupPath -TimeoutSec 900
    }
}
$setupLength = (Get-Item -LiteralPath $setupPath).Length
$setupHash = (Get-FileHash -LiteralPath $setupPath -Algorithm SHA256).Hash.ToLower()
Write-Note ('file:   ' + $setupPath)
Write-Note ('size:   ' + $setupLength)
Write-Note ('sha256: ' + $setupHash)
if ($setupLength -ne $script:PimeSetupSize) {
    throw ('PIME installer has an unexpected size ({0} != {1}). Delete the cache and retry.' -f $setupLength, $script:PimeSetupSize)
}
if ($setupHash -ne $script:PimeSetupSha256) {
    throw 'PIME installer checksum mismatch. Delete build\cache and retry.'
}

# ---------------------------------------------------------------------------
# embedded Rime runtime + precompiled rime-frost data
# ---------------------------------------------------------------------------

Write-Step 'Preparing embedded Rime runtime and rime-frost data'
$prepareScript = Join-Path (Split-Path -Parent $bundleDir) 'prepare_rime.ps1'
Assert-File $prepareScript 'prepare_rime.ps1'
$prepareArgs = @{ TargetDir = $rimeStageDir }
if ($SkipDownload) { $prepareArgs['SkipDownload'] = $true }
& $prepareScript @prepareArgs
Assert-File (Join-Path $rimeStageDir 'rime_runtime\rime.dll') 'staged rime.dll'
Assert-File (Join-Path $rimeStageDir 'rime_data\build\rime_frost.table.bin') 'precompiled rime_frost.table.bin'

# ---------------------------------------------------------------------------
# payload
# ---------------------------------------------------------------------------

Write-Step 'Assembling the payload'
if (Test-Path -LiteralPath $payloadDir) {
    Remove-Item -LiteralPath $payloadDir -Recurse -Force
}
New-Item -ItemType Directory -Path $payloadDir -Force | Out-Null

foreach ($name in @('install.ps1', 'uninstall.ps1', 'common.ps1', 'THIRD_PARTY_NOTICES.md')) {
    $source = Join-Path $bundleDir $name
    Assert-File $source $name
    Copy-Item -LiteralPath $source -Destination $payloadDir -Force
}

$moduleTarget = Join-Path $payloadDir 'module'
Copy-Item -LiteralPath $srcModule -Destination $moduleTarget -Recurse -Force
foreach ($cache in @(Get-ChildItem -LiteralPath $moduleTarget -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue)) {
    Remove-Item -LiteralPath $cache.FullName -Recurse -Force -ErrorAction SilentlyContinue
}
# Replace any local dev rime_runtime/rime_data with the cleanly staged copies.
foreach ($rimeSub in @('rime_runtime', 'rime_data')) {
    $destSub = Join-Path $moduleTarget $rimeSub
    if (Test-Path -LiteralPath $destSub) {
        Remove-Item -LiteralPath $destSub -Recurse -Force
    }
    Copy-Item -LiteralPath (Join-Path $rimeStageDir $rimeSub) -Destination $destSub -Recurse -Force
}

foreach ($required in @(
    'ime.json',
    'profiles.json',
    'cips_ime.py',
    'ui_settings.py',
    'icon.ico',
    'core\__init__.py',
    'win32\__init__.py',
    'win32\candidate_ui.py',
    'rime_engine\__init__.py',
    'rime_config\cips.yaml',
    'rime_config\gram_patch.yaml',
    'skins\gemini_neko_mascot.png',
    'rime_runtime\rime.dll',
    'rime_data\build\rime_frost.table.bin'
)) {
    Assert-File (Join-Path $moduleTarget $required) ('module\' + $required)
}

$licenses = Join-Path $payloadDir 'licenses'
New-Item -ItemType Directory -Path $licenses -Force | Out-Null
$licenseSources = @(
    @{ From = '_pime\LGPL-2.0.txt'; To = 'LGPL-2.0.txt' },
    @{ From = '_pime\PSF.txt'; To = 'PSF.txt' },
    @{ From = '_pime\APACHE-2.0.txt'; To = 'APACHE-2.0.txt' },
    @{ From = '_pime\LICENSE.txt'; To = 'LICENSE-PIME.txt' }
)
foreach ($item in $licenseSources) {
    $source = Join-Path $repoRoot $item.From
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $licenses $item.To) -Force
    } else {
        Write-Note ('license text missing, skipped: ' + $item.From)
    }
}
$frostLicense = Join-Path $rimeStageDir 'rime_data\LICENSE'
if (Test-Path -LiteralPath $frostLicense) {
    Copy-Item -LiteralPath $frostLicense -Destination (Join-Path $licenses 'GPL-3.0-rime-frost.txt') -Force
}
if (@(Get-ChildItem -LiteralPath $licenses -File).Count -eq 0) {
    throw 'No license texts were collected; refusing to build a redistributable bundle.'
}

Copy-Item -LiteralPath $setupPath -Destination (Join-Path $payloadDir $script:PimeSetupName) -Force

$bundleJson = [ordered]@{
    product          = 'cips-input'
    version          = $version
    pimeVersion      = $pimeVersion
    pimeSetup        = $script:PimeSetupName
    pimeSetupSha256  = $setupHash
    pimeSetupSize    = $setupLength
    gramFile         = $script:GramFileName
    gramUrl          = $script:GramUrl
    gramSha256       = $script:GramSha256
    gramSize         = $script:GramSize
    builtAt          = (Get-Date).ToString('o')
    builtBy          = 'deploy/bundle/build_installer.ps1'
}
$bundleJson | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $payloadDir 'bundle.json') -Encoding UTF8

$payloadFiles = @(Get-ChildItem -LiteralPath $payloadDir -Recurse -File)
$payloadBytes = ($payloadFiles | Measure-Object -Property Length -Sum).Sum
Write-Note ('{0} files, {1:N1} MB uncompressed' -f $payloadFiles.Count, ($payloadBytes / 1MB))

# ---------------------------------------------------------------------------
# verify payload scripts parse
# ---------------------------------------------------------------------------

Write-Step 'Checking the payload scripts'
foreach ($name in @('common.ps1', 'install.ps1', 'uninstall.ps1')) {
    $path = Join-Path $payloadDir $name
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
    if ($errors -and $errors.Count -gt 0) {
        throw ('PowerShell syntax errors in {0}: {1}' -f $name, ($errors | ForEach-Object { $_.Message } -join '; '))
    }
    Write-Note ('parsed ' + $name)
}

# ---------------------------------------------------------------------------
# build the bootstrapper
# ---------------------------------------------------------------------------

Write-Step 'Compiling the installer bootstrapper'
if (Test-Path -LiteralPath $stageDir) {
    Remove-Item -LiteralPath $stageDir -Recurse -Force
}
New-Item -ItemType Directory -Path $stageDir -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $bundleDir 'installer\Installer.cs') -Destination $stageDir -Force
Copy-Item -LiteralPath (Join-Path $bundleDir 'installer\app.manifest') -Destination $stageDir -Force
Copy-Item -LiteralPath (Join-Path $srcModule 'icon.ico') -Destination (Join-Path $stageDir 'icon.ico') -Force

Add-Type -AssemblyName System.IO.Compression.FileSystem
$zipPath = Join-Path $stageDir 'payload.zip'
if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
[IO.Compression.ZipFile]::CreateFromDirectory(
    $payloadDir,
    $zipPath,
    [IO.Compression.CompressionLevel]::Optimal,
    $false
)
Write-Note ('payload.zip: {0:N1} MB' -f ((Get-Item -LiteralPath $zipPath).Length / 1MB))

$stageExe = Join-Path $stageDir $exeName
$cscArgs = @(
    '/nologo',
    '/target:winexe',
    '/platform:anycpu',
    '/optimize+',
    '/codepage:65001',
    '/win32icon:icon.ico',
    '/win32manifest:app.manifest',
    '/r:System.Windows.Forms.dll',
    '/r:System.Drawing.dll',
    '/r:System.IO.Compression.dll',
    '/r:System.IO.Compression.FileSystem.dll',
    '/resource:payload.zip,cips.payload.zip',
    ('/out:{0}' -f $exeName),
    'Installer.cs'
)
Push-Location $stageDir
try {
    & $csc @cscArgs
    $cscExit = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($cscExit -ne 0) {
    throw ('csc failed with exit code {0}' -f $cscExit)
}
Assert-File $stageExe 'compiled installer'

# ---------------------------------------------------------------------------
# self-tests
# ---------------------------------------------------------------------------

if (-not $SkipSelfTest) {
    Write-Step 'Self-test: installer dry run (default & -InstallGram)'
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $dryOutput = & $powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $payloadDir 'install.ps1') -DryRun -NoPrompt -NoRegister -NoElevate 2>&1
    $dryExit = $LASTEXITCODE
    $dryText = ($dryOutput | ForEach-Object { [string]$_ }) -join "`n"
    if ($dryExit -ne 0) {
        throw ('install.ps1 -DryRun failed with exit code {0}: {1}' -f $dryExit, $dryText)
    }
    if ($dryText -notmatch 'Dry run' -or $dryText -notmatch 'would install wanxiang gram model: False') {
        throw ('install.ps1 -DryRun did not report expected dry-run output. Output: ' + $dryText)
    }

    $dryGramOutput = & $powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $payloadDir 'install.ps1') -DryRun -InstallGram -NoPrompt -NoRegister -NoElevate 2>&1
    $dryGramExit = $LASTEXITCODE
    $dryGramText = ($dryGramOutput | ForEach-Object { [string]$_ }) -join "`n"
    if ($dryGramExit -ne 0 -or $dryGramText -notmatch 'would install wanxiang gram model: True') {
        throw ('install.ps1 -DryRun -InstallGram failed. Output: ' + $dryGramText)
    }
    Write-Note 'install.ps1 -DryRun (base & -InstallGram) OK'

    Write-Step 'Self-test: module mode layout (fake PIME tree, with optional gram)'
    $selfTestRoot = Join-Path $workDir 'selftest'
    if (Test-Path -LiteralPath $selfTestRoot) { Remove-Item -LiteralPath $selfTestRoot -Recurse -Force }
    $fakePime = Join-Path $selfTestRoot 'PIME'
    $fakeSupport = Join-Path $selfTestRoot 'support'
    $fakeGram = Join-Path $selfTestRoot 'fake.gram'
    New-Item -ItemType Directory -Path (Join-Path $fakePime 'x64') -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $fakePime 'x86') -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $fakePime 'python\input_methods') -Force | Out-Null
    $fakeBackends = '[{"name":"python","command":"python\\python3\\python.exe","workingDir":"python","params":"server.py"}]'
    Set-Content -LiteralPath (Join-Path $fakePime 'backends.json') -Value $fakeBackends -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $fakePime 'x64\PIMETextService.dll') -Value '' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $fakePime 'x86\PIMETextService.dll') -Value '' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $fakePime 'python\input_methods\__init__.py') -Value '' -Encoding ASCII
    Set-Content -LiteralPath $fakeGram -Value 'stub-gram-for-selftest' -Encoding ASCII

    $installOutput = & $powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $payloadDir 'install.ps1') `
        -InstallDir $fakePime -SupportDir $fakeSupport -InstallGram -GramPath $fakeGram -NoRegister -NoLaunch -NoPrompt -NoElevate 2>&1
    $installExit = $LASTEXITCODE
    if ($installExit -ne 0) {
        throw ('module-mode install failed with exit code {0}: {1}' -f $installExit, (($installOutput | ForEach-Object { [string]$_ }) -join "`n"))
    }
    Assert-File (Join-Path $fakePime 'python\input_methods\cips\ime.json') 'deployed module'
    Assert-File (Join-Path $fakePime 'python\input_methods\cips\rime_data\wanxiang-lts-zh-hans.gram') 'deployed gram'
    Assert-File (Join-Path $fakePime 'python\input_methods\cips\rime_data\rime_frost.custom.yaml') 'deployed gram patch'
    $markerPath = Join-Path $fakeSupport 'install.json'
    Assert-File $markerPath 'install marker'
    $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
    if ([string]$marker.mode -ne 'module' -or -not [bool]$marker.gramInstalled) {
        throw ('unexpected install marker contents: ' + (Get-Content -LiteralPath $markerPath -Raw))
    }
    Write-Note 'module mode install (+ optional gram) OK'

    $uninstallOutput = & $powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $fakeSupport 'uninstall.ps1') `
        -SupportDir $fakeSupport -InstallDir $fakePime -NoRegister -NoLaunch -NoPrompt -Force -NoElevate 2>&1
    $uninstallExit = $LASTEXITCODE
    if ($uninstallExit -ne 0) {
        throw ('module-mode uninstall failed with exit code {0}: {1}' -f $uninstallExit, (($uninstallOutput | ForEach-Object { [string]$_ }) -join "`n"))
    }
    if (Test-Path -LiteralPath (Join-Path $fakePime 'python\input_methods\cips')) {
        throw 'uninstall did not remove the cips module from the fake PIME tree.'
    }
    Write-Note 'module mode uninstall OK'

    Write-Step 'Self-test: bootstrapper /batch -DryRun'
    $batch = Start-Process -FilePath $stageExe -ArgumentList @('/batch', '-DryRun') -Wait -PassThru
    if ($batch.ExitCode -ne 0) {
        throw ('bootstrapper /batch -DryRun failed with exit code {0}' -f $batch.ExitCode)
    }
    Write-Note 'bootstrapper batch dry run OK'
} else {
    Write-Note 'self-tests skipped (-SkipSelfTest)'
}

# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------

Write-Step 'Publishing'
if (-not (Test-Path -LiteralPath $distDir)) {
    New-Item -ItemType Directory -Path $distDir -Force | Out-Null
}
$outExe = Join-Path $distDir $exeName
Copy-Item -LiteralPath $stageExe -Destination $outExe -Force
$outHash = (Get-FileHash -LiteralPath $outExe -Algorithm SHA256).Hash.ToLower()
$outSize = (Get-Item -LiteralPath $outExe).Length
$sumsPath = Join-Path $distDir 'SHA256SUMS.txt'
$existing = @()
if (Test-Path -LiteralPath $sumsPath) {
    $existing = @(Get-Content -LiteralPath $sumsPath | Where-Object { $_ -and $_ -notmatch [regex]::Escape($exeName) })
}
$existing += ('{0}  {1}' -f $outHash, $exeName)
Set-Content -LiteralPath $sumsPath -Value $existing -Encoding ASCII

if (-not $KeepStage) {
    foreach ($intermediate in @($stageDir, $payloadDir, (Join-Path $workDir 'selftest'))) {
        if (Test-Path -LiteralPath $intermediate) {
            Remove-Item -LiteralPath $intermediate -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host ''
Write-Host 'Build finished.' -ForegroundColor Green
Write-Note ('installer: ' + $outExe)
Write-Note ('size:      {0:N1} MB' -f ($outSize / 1MB))
Write-Note ('sha256:    ' + $outHash)
Write-Note ('checksums: ' + $sumsPath)
Write-Note ''
Write-Note 'The installer is self-contained for base rime-frost + PIME, and offers'
Write-Note 'attaching the Wanxiang language model (wanxiang-lts-zh-hans.gram) as an install option.'
