<#
.SYNOPSIS
    Deploy the CIPS backend into an installed PIME 2.x tree.

.DESCRIPTION
    Copies src\input_methods\cips into <PIME>\python\input_methods\cips, then
    re-registers the PIME TSF text service DLLs so that the new language
    profile (the GUID declared in ime.json) shows up in the Windows input
    method list.

.PARAMETER PimeDir
    Root of the PIME installation. Auto-detected when omitted.

.PARAMETER SkipRegister
    Copy the files but do not touch COM registration.

.PARAMETER SkipRestart
    Do not stop a running PIMELauncher afterwards.

.PARAMETER PrepareRime
    Run deploy\prepare_rime.ps1 to stage librime + precompiled rime-frost
    into the deployed backend.

.PARAMETER InstallGram
    Download/verify wanxiang-lts-zh-hans.gram (~373 MB) into rime_data and
    apply rime_frost_with_gram patches.

.PARAMETER GramPath
    Use a local .gram file instead of downloading when -InstallGram is set.

.PARAMETER SkipDownload
    Do not download upstream archives when staging Rime.

.PARAMETER Uninstall
    Remove the deployed CIPS folder and re-register (dropping the profile).
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string] $PimeDir,
    [switch] $SkipRegister,
    [switch] $SkipRestart,
    [switch] $PrepareRime,
    [switch] $InstallGram,
    [string] $GramPath,
    [switch] $SkipDownload,
    [switch] $Uninstall
)

$ErrorActionPreference = 'Stop'

$BackendSubDir = 'python\input_methods\cips'
$LauncherName  = 'PIMELauncher.exe'
$GramUrl       = 'https://github.com/amzxyz/rime-wanxiang-data/releases/download/LTS/wanxiang-lts-zh-hans.gram'
$GramSha256    = 'e9ddd501b7d536bc5ea1346f8f900ae49a719a05be349e0a17cb27e717143ca5'
$GramSize      = 390786092
$GramPatchSchemas = @(
    'rime_frost',
    'rime_frost_double_pinyin',
    'rime_frost_double_pinyin_flypy',
    'rime_frost_double_pinyin_mspy',
    'rime_frost_double_pinyin_sogou',
    'rime_frost_double_pinyin_abc',
    'rime_frost_double_pinyin_ziguang'
)

function Test-IsElevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-PimeRoot {
    param([string] $Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return $false }
    return (Test-Path -LiteralPath (Join-Path $Path 'backends.json') -PathType Leaf)
}

function Find-PimeRoot {
    $candidates = New-Object System.Collections.Generic.List[string]

    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($base) { $candidates.Add((Join-Path $base 'PIME')) }
    }

    $uninstallRoots = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
    )
    foreach ($root in $uninstallRoots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        foreach ($key in Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue) {
            $props = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue
            if (-not $props) { continue }
            if (-not ($props.DisplayName -match 'PIME')) { continue }
            if ($props.InstallLocation) { $candidates.Add($props.InstallLocation.TrimEnd('\')) }
        }
    }

    foreach ($candidate in $candidates) {
        if (Test-PimeRoot -Path $candidate) { return $candidate }
    }
    return $null
}

function Get-Regsvr32Paths {
    $windows = $env:SystemRoot
    if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) {
        return @{
            X64 = Join-Path $windows 'Sysnative\regsvr32.exe'
            X86 = Join-Path $windows 'SysWOW64\regsvr32.exe'
        }
    }
    if ([Environment]::Is64BitOperatingSystem) {
        return @{
            X64 = Join-Path $windows 'System32\regsvr32.exe'
            X86 = Join-Path $windows 'SysWOW64\regsvr32.exe'
        }
    }
    return @{ X64 = $null; X86 = Join-Path $windows 'System32\regsvr32.exe' }
}

function Invoke-Registration {
    param([string] $Root)

    $regsvr = Get-Regsvr32Paths
    $targets = @(
        @{ Arch = 'x64'; Dll = Join-Path $Root 'x64\PIMETextService.dll'; Exe = $regsvr.X64 },
        @{ Arch = 'x86'; Dll = Join-Path $Root 'x86\PIMETextService.dll'; Exe = $regsvr.X86 }
    )

    $any = $false
    foreach ($target in $targets) {
        if (-not $target.Exe) { continue }
        if (-not (Test-Path -LiteralPath $target.Dll -PathType Leaf)) {
            Write-Warning ("{0} text service DLL not found, skipping: {1}" -f $target.Arch, $target.Dll)
            continue
        }
        $any = $true
        if (-not $PSCmdlet.ShouldProcess($target.Dll, "regsvr32 /s ($($target.Arch))")) { continue }
        $arguments = @('/s', ('"{0}"' -f $target.Dll))
        $proc = Start-Process -FilePath $target.Exe -ArgumentList $arguments -Wait -PassThru -ErrorAction Stop
        $code = $proc.ExitCode
        if ($code -ne 0) {
            throw ("regsvr32 failed for {0} (exit code {1}). Run this script in an elevated shell." -f $target.Dll, $code)
        }
        Write-Host ("  registered {0} -> {1}" -f $target.Arch, $target.Dll)
    }

    if (-not $any) {
        throw ("No PIMETextService.dll found under {0}. Is this really a PIME 2.x install?" -f $Root)
    }
}

function Stop-Launcher {
    param([string] $Root)
    $exe = Join-Path $Root $LauncherName
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { return }
    $running = Get-Process -Name 'PIMELauncher' -ErrorAction SilentlyContinue
    if (-not $running) { return }
    if (-not $PSCmdlet.ShouldProcess('PIMELauncher', 'graceful /quit')) { return }
    try {
        & $exe /quit | Out-Null
        Start-Sleep -Milliseconds 800
        Write-Host '  stopped PIMELauncher (it restarts on demand)'
    } catch {
        Write-Warning ("Could not stop PIMELauncher: {0}" -f $_.Exception.Message)
    }
}

# ---------------------------------------------------------------- main

if (-not $PimeDir) {
    $PimeDir = Find-PimeRoot
    if (-not $PimeDir) {
        throw 'Could not locate a PIME installation. Pass -PimeDir explicitly, e.g. -PimeDir "C:\Program Files\PIME".'
    }
    Write-Host "Detected PIME at: $PimeDir"
}

if (-not (Test-PimeRoot -Path $PimeDir)) {
    throw ("'{0}' does not look like a PIME 2.x root (backends.json missing)." -f $PimeDir)
}

$dest = Join-Path $PimeDir $BackendSubDir

$destFull = [IO.Path]::GetFullPath($dest)
$expectedTail = [IO.Path]::GetFullPath((Join-Path $PimeDir $BackendSubDir))
if ($destFull -ne $expectedTail -or $destFull -notmatch 'input_methods[\\/]cips$') {
    throw ("Refusing to touch unexpected path: {0}" -f $destFull)
}

if (-not (Test-IsElevated)) {
    throw 'This script must run in an elevated (Administrator) shell: it writes under Program Files and calls regsvr32.'
}

if ($Uninstall) {
    if (Test-Path -LiteralPath $destFull) {
        if ($PSCmdlet.ShouldProcess($destFull, 'remove deployed backend')) {
            Remove-Item -LiteralPath $destFull -Recurse -Force
            Write-Host "Removed $destFull"
        }
    } else {
        Write-Host "Nothing to remove at $destFull"
    }
    if (-not $SkipRegister) { Invoke-Registration -Root $PimeDir }
    if (-not $SkipRestart)  { Stop-Launcher -Root $PimeDir }
    Write-Host 'Uninstalled. The cips-input entry disappears from the input method list after re-registration.'
    return
}

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$source = Join-Path $repoRoot 'src\input_methods\cips'
if (-not (Test-Path -LiteralPath $source -PathType Container)) {
    throw ("Source backend not found: {0}" -f $source)
}
if (-not (Test-Path -LiteralPath (Join-Path $source 'ime.json') -PathType Leaf)) {
    throw ("ime.json missing in source backend: {0}" -f $source)
}

$rimeStage = $null
if ($PrepareRime) {
    $prepareScript = Join-Path $PSScriptRoot 'prepare_rime.ps1'
    $rimeStage = Join-Path $repoRoot 'build\bundle\rime_stage'
    $prepArgs = @{ TargetDir = $rimeStage }
    if ($SkipDownload) { $prepArgs['SkipDownload'] = $true }
    & $prepareScript @prepArgs
}

Write-Host "Source: $source"
Write-Host "Target: $destFull"

$wantGram = [bool]$InstallGram -or (-not [string]::IsNullOrWhiteSpace($GramPath))

if ($PSCmdlet.ShouldProcess($destFull, 'replace backend folder')) {
    $preservedRime = @{}
    if (-not $PrepareRime -and (Test-Path -LiteralPath $destFull)) {
        # Preserve already-deployed rime_runtime / rime_data when updating Python files.
        foreach ($sub in @('rime_runtime', 'rime_data')) {
            $oldSub = Join-Path $destFull $sub
            if (Test-Path -LiteralPath $oldSub) {
                $tmpSub = Join-Path $env:TEMP ('cips-preserve-' + $sub + '-' + [Guid]::NewGuid().ToString('N'))
                Move-Item -LiteralPath $oldSub -Destination $tmpSub -Force
                $preservedRime[$sub] = $tmpSub
            }
        }
    }

    if (Test-Path -LiteralPath $destFull) {
        Remove-Item -LiteralPath $destFull -Recurse -Force
    }
    New-Item -ItemType Directory -Path $destFull -Force | Out-Null

    Copy-Item -Path (Join-Path $source '*') -Destination $destFull -Recurse -Force
    Get-ChildItem -LiteralPath $destFull -Recurse -Force -Directory -Filter '__pycache__' |
        Remove-Item -Recurse -Force

    if ($rimeStage) {
        foreach ($sub in @('rime_runtime', 'rime_data')) {
            $dstSub = Join-Path $destFull $sub
            if (Test-Path -LiteralPath $dstSub) { Remove-Item -LiteralPath $dstSub -Recurse -Force }
            Copy-Item -LiteralPath (Join-Path $rimeStage $sub) -Destination $dstSub -Recurse -Force
        }
    } else {
        foreach ($kv in $preservedRime.GetEnumerator()) {
            $dstSub = Join-Path $destFull $kv.Key
            if (-not (Test-Path -LiteralPath $dstSub)) {
                Move-Item -LiteralPath $kv.Value -Destination $dstSub -Force
            } else {
                Remove-Item -LiteralPath $kv.Value -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    }
    Write-Host '  copied backend files'
}

if ($wantGram) {
    $dataDir = Join-Path $destFull 'rime_data'
    $runtimeDir = Join-Path $destFull 'rime_runtime'
    if (-not (Test-Path -LiteralPath $dataDir)) {
        throw "rime_data missing in target; re-run with -PrepareRime to install Rime Frost first."
    }
    $destGram = Join-Path $dataDir 'wanxiang-lts-zh-hans.gram'
    if ($GramPath) {
        Copy-Item -LiteralPath ([IO.Path]::GetFullPath($GramPath)) -Destination $destGram -Force
    } else {
        $cacheGram = Join-Path $repoRoot 'build\cache\wanxiang-lts-zh-hans.gram'
        if (-not (Test-Path -LiteralPath $cacheGram)) {
            if ($SkipDownload) { throw "wanxiang-lts-zh-hans.gram not cached and -SkipDownload was specified." }
            New-Item -ItemType Directory -Path (Split-Path -Parent $cacheGram) -Force | Out-Null
            Write-Host "  downloading $GramUrl (~373 MB)..."
            $ProgressPreference = 'SilentlyContinue'
            Invoke-WebRequest -Uri $GramUrl -OutFile $cacheGram -TimeoutSec 1800
        }
        $actual = (Get-FileHash -LiteralPath $cacheGram -Algorithm SHA256).Hash.ToLower()
        if ($actual -ne $GramSha256) {
            throw ("wanxiang-lts-zh-hans.gram checksum mismatch ({0} != {1})" -f $actual, $GramSha256)
        }
        Copy-Item -LiteralPath $cacheGram -Destination $destGram -Force
    }

    $patchSrc = Join-Path $destFull 'rime_config\gram_patch.yaml'
    foreach ($schema in $GramPatchSchemas) {
        Copy-Item -LiteralPath $patchSrc -Destination (Join-Path $dataDir ($schema + '.custom.yaml')) -Force
    }
    $deployer = Join-Path $runtimeDir 'rime_deployer.exe'
    if (Test-Path -LiteralPath $deployer) {
        $oldPath = $env:PATH
        try {
            $env:PATH = $runtimeDir + ';' + $oldPath
            & $deployer --build $dataDir $dataDir (Join-Path $dataDir 'build') | Out-Null
        } finally {
            $env:PATH = $oldPath
        }
        foreach ($ephemeral in @('user.yaml', 'installation.yaml')) {
            $ep = Join-Path $dataDir $ephemeral
            if (Test-Path -LiteralPath $ep) { Remove-Item -LiteralPath $ep -Force }
        }
    }
    Write-Host '  enabled wanxiang-lts-zh-hans.gram'
}

if (-not $SkipRegister) { Invoke-Registration -Root $PimeDir }
if (-not $SkipRestart)  { Stop-Launcher -Root $PimeDir }

Write-Host ''
Write-Host 'Done. Next steps:'
Write-Host '  1. Press Win+Space (or click the language indicator) and pick "cips-input".'
Write-Host '  2. Diagnostics: %LOCALAPPDATA%\PIME\Logs\cips.log'
Write-Host '  3. Rime user data: %APPDATA%\cips-input\rime'
