# Prepares the embedded Rime runtime (librime 1.17.0 x86) and precompiled
# rime-frost 1.0.6 data for cips-input.
#
# Caches upstream archives in build\cache and verifies their exact sizes and
# SHA-256 digests. After the first download, runs work completely offline.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File deploy\prepare_rime.ps1 -InPlace
#   powershell -ExecutionPolicy Bypass -File deploy\prepare_rime.ps1 -TargetDir build\bundle\rime_stage
#
# ASCII only: Windows PowerShell 5.1 parses BOM-less scripts using the system
# ANSI code page.

[CmdletBinding()]
param(
    [string]$TargetDir,
    [switch]$InPlace,
    [switch]$SkipDownload,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$script:LibrimeArchiveName = 'rime-33e7814-Windows-msvc-x86.7z'
$script:LibrimeUrl         = 'https://github.com/rime/librime/releases/download/1.17.0/rime-33e7814-Windows-msvc-x86.7z'
$script:LibrimeSha256      = 'af235c26c06152ce09ceb8fe9d9ab9fba7ab43ce30aa952b40174d806f5cc3d9'
$script:LibrimeSize        = 7183925

$script:FrostArchiveName   = 'rime-frost-1.0.6-schemas.zip'
$script:FrostUrl           = 'https://github.com/gaboolic/rime-frost/releases/download/1.0.6/rime-frost-schemas.zip'
$script:FrostSha256        = 'ef588b6ed789e07125869cc6612961de849b1947dc352e6a8364e91d67ce0a4a'
$script:FrostSize          = 44261129

$repoRoot = Split-Path -Parent $PSScriptRoot
$cacheDir = Join-Path $repoRoot 'build\cache'

if ($InPlace) {
    $TargetDir = Join-Path $repoRoot 'src\input_methods\cips'
} elseif (-not $TargetDir) {
    $TargetDir = Join-Path $repoRoot 'build\bundle\rime_stage'
} else {
    $TargetDir = [IO.Path]::GetFullPath($TargetDir)
}

function Write-Info {
    param([string]$Message)
    Write-Host ('    ' + $Message)
}

function Resolve-CachedArchive {
    param(
        [string]$Name,
        [string]$Url,
        [long]$ExpectedSize,
        [string]$ExpectedSha256
    )
    $path = Join-Path $cacheDir $Name
    if (-not (Test-Path -LiteralPath $path)) {
        if ($SkipDownload) {
            throw ('Archive not cached ({0}) and -SkipDownload was given.' -f $path)
        }
        New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null
        Write-Info ('downloading ' + $Url)
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $Url -OutFile $path -TimeoutSec 600
    }
    $len = (Get-Item -LiteralPath $path).Length
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower()
    if ($len -ne $ExpectedSize) {
        throw ('{0} has unexpected size ({1} != {2}). Delete build\cache and retry.' -f $Name, $len, $ExpectedSize)
    }
    if ($hash -ne $ExpectedSha256) {
        throw ('{0} checksum mismatch ({1} != {2}). Delete build\cache and retry.' -f $Name, $hash, $ExpectedSha256)
    }
    Write-Info ('verified ' + $Name + ' (' + $hash.Substring(0, 12) + '...)')
    return $path
}

function Expand-ArchiveWithTar {
    param(
        [string]$ArchivePath,
        [string]$Destination
    )
    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $tar = Join-Path $env:SystemRoot 'System32\tar.exe'
    if (Test-Path -LiteralPath $tar) {
        & $tar -xf $ArchivePath -C $Destination
        if ($LASTEXITCODE -eq 0) { return }
    }
    $sevenZip = $null
    foreach ($candidate in @('7z.exe', 'C:\Program Files\7-Zip\7z.exe', 'C:\Program Files (x86)\7-Zip\7z.exe')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) { $sevenZip = $cmd.Source; break }
    }
    if ($sevenZip) {
        & $sevenZip x -y ('-o' + $Destination) $ArchivePath | Out-Null
        if ($LASTEXITCODE -eq 0) { return }
    }
    throw ('Failed to extract archive: ' + $ArchivePath)
}

$runtimeDir = Join-Path $TargetDir 'rime_runtime'
$dataDir    = Join-Path $TargetDir 'rime_data'
$stampPath  = Join-Path $dataDir '.cips-rime-stamp.json'
$configDir  = Join-Path $repoRoot 'src\input_methods\cips\rime_config'

$stampExpected = ('{0}|{1}|{2}|{3}' -f $script:LibrimeSha256, $script:FrostSha256, (Get-FileHash (Join-Path $configDir 'default.custom.yaml') -Algorithm SHA256).Hash.ToLower(), (Get-FileHash (Join-Path $configDir 'cips.yaml') -Algorithm SHA256).Hash.ToLower())

if (-not $Force -and (Test-Path -LiteralPath (Join-Path $runtimeDir 'rime.dll')) -and (Test-Path -LiteralPath (Join-Path $dataDir 'build\rime_frost.table.bin')) -and (Test-Path -LiteralPath $stampPath)) {
    try {
        $prev = (Get-Content -LiteralPath $stampPath -Raw | ConvertFrom-Json).stamp
        if ($prev -eq $stampExpected) {
            Write-Info ('Rime runtime and prebuilt data already up-to-date in ' + $TargetDir)
            return
        }
    } catch { }
}

$librimeArchive = Resolve-CachedArchive -Name $script:LibrimeArchiveName -Url $script:LibrimeUrl -ExpectedSize $script:LibrimeSize -ExpectedSha256 $script:LibrimeSha256
$frostArchive   = Resolve-CachedArchive -Name $script:FrostArchiveName   -Url $script:FrostUrl   -ExpectedSize $script:FrostSize   -ExpectedSha256 $script:FrostSha256

$scratch = Join-Path $repoRoot 'build\rime_extract_tmp'
if (Test-Path -LiteralPath $scratch) {
    Remove-Item -LiteralPath $scratch -Recurse -Force
}
New-Item -ItemType Directory -Path $scratch -Force | Out-Null

try {
    $libOut   = Join-Path $scratch 'librime'
    $frostOut = Join-Path $scratch 'frost'
    Write-Info 'extracting librime 1.17.0 (x86)...'
    Expand-ArchiveWithTar -ArchivePath $librimeArchive -Destination $libOut
    Write-Info 'extracting rime-frost 1.0.6 schemas...'
    Expand-ArchiveWithTar -ArchivePath $frostArchive -Destination $frostOut

    if (Test-Path -LiteralPath $runtimeDir) { Remove-Item -LiteralPath $runtimeDir -Recurse -Force }
    if (Test-Path -LiteralPath $dataDir)    { Remove-Item -LiteralPath $dataDir    -Recurse -Force }
    New-Item -ItemType Directory -Path $runtimeDir, $dataDir -Force | Out-Null

    $rimeDll = Get-ChildItem -LiteralPath $libOut -Recurse -Filter 'rime.dll' | Select-Object -First 1
    $deployer = Get-ChildItem -LiteralPath $libOut -Recurse -Filter 'rime_deployer.exe' | Select-Object -First 1
    if (-not $rimeDll -or -not $deployer) {
        throw 'rime.dll or rime_deployer.exe missing from librime archive.'
    }
    Copy-Item -LiteralPath $rimeDll.FullName -Destination (Join-Path $runtimeDir 'rime.dll') -Force
    Copy-Item -LiteralPath $deployer.FullName -Destination (Join-Path $runtimeDir 'rime_deployer.exe') -Force

    foreach ($sub in @('cn_dicts', 'cn_dicts_cell', 'en_dicts', 'lua', 'opencc')) {
        $srcSub = Join-Path $frostOut $sub
        if (-not (Test-Path -LiteralPath $srcSub)) {
            throw ('Missing expected rime-frost directory: ' + $sub)
        }
        Copy-Item -LiteralPath $srcSub -Destination (Join-Path $dataDir $sub) -Recurse -Force
    }

    $frostFiles = @(
        'default.yaml',
        'key_bindings.yaml',
        'punctuation.yaml',
        'symbols_v.yaml',
        'essay.txt',
        'LICENSE',
        'melt_eng.dict.yaml',
        'melt_eng.schema.yaml',
        'melt_eng_algebra.yaml',
        'melt_eng_algebra_common.yaml',
        'radical_pinyin.dict.yaml',
        'radical_pinyin.schema.yaml',
        'radical_pinyin_algebra.yaml',
        'rime_frost.dict.yaml',
        'rime_frost.schema.yaml',
        'rime_frost_aux.dict.yaml',
        'rime_frost_aux.schema.yaml',
        'rime_frost_double_pinyin.schema.yaml',
        'rime_frost_double_pinyin_flypy.schema.yaml',
        'rime_frost_double_pinyin_mspy.schema.yaml',
        'rime_frost_double_pinyin_sogou.schema.yaml',
        'rime_frost_double_pinyin_abc.schema.yaml',
        'rime_frost_double_pinyin_ziguang.schema.yaml'
    )
    foreach ($name in $frostFiles) {
        $srcFile = Join-Path $frostOut $name
        if (-not (Test-Path -LiteralPath $srcFile)) {
            throw ('Missing expected rime-frost file: ' + $name)
        }
        Copy-Item -LiteralPath $srcFile -Destination (Join-Path $dataDir $name) -Force
    }

    foreach ($cfg in @('cips.yaml', 'default.custom.yaml')) {
        $srcCfg = Join-Path $configDir $cfg
        if (-not (Test-Path -LiteralPath $srcCfg)) {
            throw ('Missing cips rime_config file: ' + $cfg)
        }
        Copy-Item -LiteralPath $srcCfg -Destination (Join-Path $dataDir $cfg) -Force
    }

    Write-Info 'precompiling rime-frost dictionaries and schemas...'
    $buildOut = Join-Path $dataDir 'build'
    New-Item -ItemType Directory -Path $buildOut -Force | Out-Null
    $oldPath = $env:PATH
    try {
        $env:PATH = $runtimeDir + ';' + $oldPath
        $proc = Start-Process -FilePath (Join-Path $runtimeDir 'rime_deployer.exe') `
            -ArgumentList @('--build', ('"{0}"' -f $dataDir), ('"{0}"' -f $dataDir), ('"{0}"' -f $buildOut)) `
            -Wait -PassThru -NoNewWindow
        if ($proc.ExitCode -ne 0) {
            throw ('rime_deployer.exe --build failed with exit code {0}' -f $proc.ExitCode)
        }
    } finally {
        $env:PATH = $oldPath
    }

    # Remove per-machine user/installation files created by running deployer with userdir == shared_dir.
    foreach ($ephemeral in @('user.yaml', 'installation.yaml')) {
        $epPath = Join-Path $dataDir $ephemeral
        if (Test-Path -LiteralPath $epPath) {
            Remove-Item -LiteralPath $epPath -Force
        }
    }

    if (-not (Test-Path -LiteralPath (Join-Path $buildOut 'rime_frost.table.bin'))) {
        throw 'Precompilation finished but rime_frost.table.bin was not produced.'
    }

    @{ stamp = $stampExpected; librime = '1.17.0'; rimeFrost = '1.0.6' } | ConvertTo-Json |
        Set-Content -LiteralPath $stampPath -Encoding UTF8

    Write-Info ('prepared Rime runtime and data under ' + $TargetDir)
} finally {
    if (Test-Path -LiteralPath $scratch) {
        Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
    }
}
