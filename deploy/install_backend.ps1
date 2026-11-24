<#
.SYNOPSIS
    Deploy the CIPS backend into an installed PIME 2.x tree.

.DESCRIPTION
    Copies src\input_methods\cips into <PIME>\python\input_methods\cips, then
    re-registers the PIME TSF text service DLLs so that the new language
    profile (the GUID declared in ime.json) shows up in the Windows input
    method list.

    Why re-registration is required: PIME discovers input methods by scanning
    <programDir>\<backendDir>\input_methods\*\ime.json inside
    PIMETextService's DllRegisterServer (see PIMETextService/DllEntry.cpp).
    Dropping a folder on disk alone does not create the TSF language profile,
    so the IME never appears to Windows. The official installer performs the
    same step through the RegisterTextService macro in installer/macros.nsh,
    which is exactly `regsvr32 /s` on the x64 and x86 DLLs.

.PARAMETER PimeDir
    Root of the PIME installation. Auto-detected when omitted.

.PARAMETER SkipRegister
    Copy the files but do not touch COM registration.

.PARAMETER SkipRestart
    Do not stop a running PIMELauncher afterwards.

.PARAMETER Uninstall
    Remove the deployed CIPS folder and re-register (dropping the profile).

.EXAMPLE
    .\install_backend.ps1

.EXAMPLE
    .\install_backend.ps1 -PimeDir 'D:\Apps\PIME' -SkipRegister
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string] $PimeDir,
    [switch] $SkipRegister,
    [switch] $SkipRestart,
    [switch] $Uninstall
)

$ErrorActionPreference = 'Stop'

$BackendSubDir = 'python\input_methods\cips'
$LauncherName  = 'PIMELauncher.exe'

function Test-IsElevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-PimeRoot {
    param([string] $Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return $false }
    # backends.json plus the TSF DLLs are the two things any PIME 2.x tree has.
    return (Test-Path -LiteralPath (Join-Path $Path 'backends.json') -PathType Leaf)
}

function Find-PimeRoot {
    $candidates = New-Object System.Collections.Generic.List[string]

    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($base) { $candidates.Add((Join-Path $base 'PIME')) }
    }

    # Fall back to the uninstall registry entries in case PIME was installed
    # somewhere non-standard.
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

# Resolve the 64-bit and 32-bit regsvr32 explicitly. PIME needs both DLLs
# registered: a 32-bit host process loads the x86 text service, a 64-bit host
# loads the x64 one. Under a 32-bit PowerShell, System32 is redirected to
# SysWOW64, so the native one is reached through Sysnative.
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
        # regsvr32 is a GUI-subsystem binary: in Windows PowerShell the call
        # operator does not wait for it and leaves $LASTEXITCODE unset, which
        # makes a successful registration look like a failure. Start-Process
        # with -Wait is the only reliable way to get its exit code.
        $arguments = @('/s', ('"{0}"' -f $target.Dll))
        $proc = Start-Process -FilePath $target.Exe -ArgumentList $arguments -Wait -PassThru -ErrorAction Stop
        $code = $proc.ExitCode
        if ($code -ne 0) {
            # regsvr32 with /s reports failure only through the exit code; it
            # pops no dialog, so surface the number here.
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
        # Documented graceful shutdown switch; the launcher will be restarted
        # on demand by the text service.
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

# Guard: never operate on a path that is not the CIPS backend folder inside an
# input_methods directory. This protects the recursive delete below.
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

$source = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\src\input_methods\cips'))
if (-not (Test-Path -LiteralPath $source -PathType Container)) {
    throw ("Source backend not found: {0}" -f $source)
}
if (-not (Test-Path -LiteralPath (Join-Path $source 'ime.json') -PathType Leaf)) {
    throw ("ime.json missing in source backend: {0}" -f $source)
}

Write-Host "Source: $source"
Write-Host "Target: $destFull"

if ($PSCmdlet.ShouldProcess($destFull, 'replace backend folder')) {
    if (Test-Path -LiteralPath $destFull) {
        # Replace rather than merge so that deleted/renamed modules do not
        # linger from a previous deployment.
        Remove-Item -LiteralPath $destFull -Recurse -Force
    }
    New-Item -ItemType Directory -Path $destFull -Force | Out-Null

    Copy-Item -Path (Join-Path $source '*') -Destination $destFull -Recurse -Force
    # Byte-code caches are machine specific and can mask a stale module.
    Get-ChildItem -LiteralPath $destFull -Recurse -Force -Directory -Filter '__pycache__' |
        Remove-Item -Recurse -Force
    Write-Host '  copied backend files'
}

if (-not $SkipRegister) { Invoke-Registration -Root $PimeDir }
if (-not $SkipRestart)  { Stop-Launcher -Root $PimeDir }

Write-Host ''
Write-Host 'Done. Next steps:'
Write-Host '  1. Press Win+Space (or click the language indicator) and pick "cips-input".'
Write-Host '  2. If PIME was never installed on this machine, run the official installer once first.'
Write-Host '  3. Diagnostics: %LOCALAPPDATA%\PIME\Logs\cips.log (uncaught backend errors: python_backend.log)'
Write-Host '  4. Edit profiles.json in the deployed folder to tune per-app pacing; no restart needed.'
