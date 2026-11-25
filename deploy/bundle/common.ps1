# Shared helpers for the cips-input installer and uninstaller.
#
# This file is copied next to install.ps1 / uninstall.ps1 into the payload and
# into the permanent support folder (%ProgramData%\cips-input), so both scripts
# can dot-source it. It must stay dependency-free (Windows PowerShell 5.1,
# built-in modules only) and ASCII-only.
#
# Contract of callers:
#   * install.ps1  : dot-sources this file, creates the support folder.
#   * uninstall.ps1: dot-sources this file, reads install.json from its folder.

$script:ProductName = 'cips-input'
$script:SupportFolderName = 'cips-input'
$script:MarkerName = 'install.json'
$script:LogPath = Join-Path $env:TEMP 'cips-input-install.log'

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    try {
        $line = '[{0}] [{1}] {2}' -f (Get-Date -Format 'HH:mm:ss'), $Level, $Message
        Add-Content -LiteralPath $script:LogPath -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
    } catch {
        # Diagnostics must never break the installer.
    }
}

function Write-Step {
    param([string]$Message)
    Write-Host ('[STEP] ' + $Message) -ForegroundColor Cyan
    Write-Log $Message 'STEP'
}

function Write-Note {
    param([string]$Message)
    Write-Host ('       ' + $Message)
    Write-Log $Message 'INFO'
}

function Write-Warn {
    param([string]$Message)
    Write-Host ('[WARN] ' + $Message) -ForegroundColor Yellow
    Write-Log $Message 'WARN'
}

function Fail {
    param([string]$Message)
    Write-Host ('[FAIL] ' + $Message) -ForegroundColor Red
    Write-Log $Message 'FAIL'
    throw $Message
}

function Get-Sha256Hex {
    param([string]$LiteralPath)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($LiteralPath)
    try {
        $bytes = $sha.ComputeHash($stream)
        return ([System.BitConverter]::ToString($bytes) -replace '-', '').ToLowerInvariant()
    } finally {
        $stream.Dispose()
        $sha.Dispose()
    }
}

function Test-Administrator {
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false
    }
}

function Get-PowerShellExe {
    return (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')
}

# Relaunches the given script elevated and waits for it. Returns its exit code.
function Invoke-SelfElevation {
    param(
        [string]$ScriptPath,
        [string[]]$ExtraArgs = @()
    )
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $ScriptPath), '-Elevated')
    foreach ($arg in $ExtraArgs) {
        if ($arg) { $argList += $arg }
    }
    Write-Host 'Administrator rights are required. Requesting elevation...' -ForegroundColor Yellow
    $proc = Start-Process -FilePath (Get-PowerShellExe) -Verb RunAs -ArgumentList $argList -Wait -PassThru
    return $proc.ExitCode
}

function Test-Windows10 {
    return ([Environment]::OSVersion.Version.Build -ge 10240)
}

function Get-WindowsArch {
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') {
        return 'arm64'
    }
    if ([Environment]::Is64BitOperatingSystem) {
        return 'x64'
    }
    return 'x86'
}

# Candidate installation folders, in the order PIME's C++ client resolves them:
# %ProgramFiles(x86)%\PIME, then %ProgramFiles%\PIME.
function Get-PimeCandidates {
    $list = New-Object System.Collections.Generic.List[string]
    foreach ($folder in @([Environment+SpecialFolder]::ProgramFilesX86, [Environment+SpecialFolder]::ProgramFiles)) {
        try {
            $base = [Environment]::GetFolderPath($folder)
            if ($base) {
                $candidate = Join-Path $base 'PIME'
                if (-not $list.Contains($candidate)) { $list.Add($candidate) }
            }
        } catch {
            # A missing special folder is not fatal.
        }
    }
    return $list
}

function Get-PimeInstallDir {
    param([string]$Override)
    if ($Override) {
        return [IO.Path]::GetFullPath($Override)
    }
    # An existing installation wins, because PIME hardcodes the folder when it
    # scans for language profiles at registration time.
    $candidates = @(Get-PimeCandidates)
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    $registry = Get-Item 'HKLM:\SOFTWARE\PIME' -ErrorAction SilentlyContinue
    if ($registry) {
        $value = [string]$registry.GetValue('')
        if ($value -and (Test-Path -LiteralPath $value)) { return $value }
    }
    return $candidates[0]
}

function Test-PimeRuntime {
    param([string]$Dir)
    if (-not (Test-Path -LiteralPath (Join-Path $Dir 'backends.json'))) { return $false }
    foreach ($relative in @('x64\PIMETextService.dll', 'x86\PIMETextService.dll', 'arm64\PIMETextService.dll')) {
        if (Test-Path -LiteralPath (Join-Path $Dir $relative)) { return $true }
    }
    return $false
}

# The Python backend directory, as declared by the installation's backends.json.
function Get-PythonBackendDir {
    param([string]$Dir)
    $backendsPath = Join-Path $Dir 'backends.json'
    if (-not (Test-Path -LiteralPath $backendsPath)) {
        Fail ('backends.json not found under ' + $Dir)
    }
    try {
        $backends = Get-Content -LiteralPath $backendsPath -Raw | ConvertFrom-Json
    } catch {
        Fail ('Cannot parse backends.json: ' + $_.Exception.Message)
    }
    $python = @($backends) | Where-Object { $_.name -eq 'python' } | Select-Object -First 1
    if (-not $python) {
        Fail 'This PIME installation has no python backend; cips-input cannot be deployed.'
    }
    return (Join-Path $Dir ([string]$python.workingDir))
}

function Invoke-RegSvr {
    param(
        [string]$Dll,
        [string]$Arch,
        [switch]$Unregister
    )
    if (-not (Test-Path -LiteralPath $Dll)) {
        Write-Warn ('DLL not found, skipping: ' + $Dll)
        return
    }
    $regsvr = Join-Path $env:SystemRoot 'System32\regsvr32.exe'
    if ($Arch -eq 'x86') {
        $wow = Join-Path $env:SystemRoot 'SysWOW64\regsvr32.exe'
        if (Test-Path -LiteralPath $wow) { $regsvr = $wow }
    }
    $arguments = @('/s', ('"{0}"' -f $Dll))
    if ($Unregister) { $arguments = @('/u', '/s', ('"{0}"' -f $Dll)) }
    $proc = Start-Process -FilePath $regsvr -ArgumentList $arguments -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        Fail ('regsvr32 failed for {0} (exit code {1})' -f $Dll, $proc.ExitCode)
    }
    if ($Unregister) {
        Write-Note ('unregistered ' + $Dll)
    } else {
        Write-Note ('registered ' + $Dll)
    }
}

function Register-TextService {
    param([string]$Dir)
    $arch = Get-WindowsArch
    if ($arch -eq 'arm64') {
        Invoke-RegSvr (Join-Path $Dir 'arm64\PIMETextService.dll') 'arm64'
    } else {
        Invoke-RegSvr (Join-Path $Dir 'x64\PIMETextService.dll') 'x64'
    }
    Invoke-RegSvr (Join-Path $Dir 'x86\PIMETextService.dll') 'x86'
}

function Unregister-TextService {
    param([string]$Dir)
    $arch = Get-WindowsArch
    if ($arch -eq 'arm64') {
        Invoke-RegSvr (Join-Path $Dir 'arm64\PIMETextService.dll') 'arm64' -Unregister
    } else {
        Invoke-RegSvr (Join-Path $Dir 'x64\PIMETextService.dll') 'x64' -Unregister
    }
    Invoke-RegSvr (Join-Path $Dir 'x86\PIMETextService.dll') 'x86' -Unregister
}

function Stop-PimeLauncher {
    param([string]$Dir)
    $procs = @(Get-Process -Name 'PIMELauncher' -ErrorAction SilentlyContinue)
    if ($procs.Count -eq 0) { return }
    Write-Note 'stopping PIMELauncher...'
    $exe = Join-Path $Dir 'PIMELauncher.exe'
    if (Test-Path -LiteralPath $exe) {
        try {
            Start-Process -FilePath $exe -ArgumentList '/quit' -Wait -ErrorAction SilentlyContinue | Out-Null
        } catch {
            # Fall through to the hard kill below.
        }
        Start-Sleep -Milliseconds 800
    }
    foreach ($proc in @(Get-Process -Name 'PIMELauncher' -ErrorAction SilentlyContinue)) {
        try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch { }
    }
    Start-Sleep -Milliseconds 400
}

function Repair-PimeAppData {
    $dataDir = Join-Path $env:APPDATA 'PIME'
    if (-not (Test-Path -LiteralPath $dataDir)) { return }
    try {
        $owner = (Get-Acl -LiteralPath $dataDir).Owner
        if ($owner -match 'Administrators') {
            Write-Warn 'PIMELauncher created %APPDATA%\PIME with administrator ownership; repairing ACLs.'
            $user = $env:USERDOMAIN + '\' + $env:USERNAME
            & icacls $dataDir /setowner $user /T /C 2>&1 | Out-Null
            & icacls $dataDir /reset /T /C 2>&1 | Out-Null
        }
    } catch {
        Write-Warn ('Could not repair %APPDATA%\PIME ownership: ' + $_.Exception.Message)
    }
}

# Starts PIMELauncher so it runs with the interactive user's normal token.
# PIME stores per-user settings under %APPDATA%\PIME; an elevated launcher
# would poison that folder (upstream issue #973), so a shell-mediated start is
# attempted first and the elevated fallback repairs the ACLs afterwards.
function Start-PimeLauncher {
    param([string]$Dir)
    $exe = Join-Path $Dir 'PIMELauncher.exe'
    if (-not (Test-Path -LiteralPath $exe)) {
        Write-Warn 'PIMELauncher.exe is missing; the input method will not run until it is restored.'
        return $false
    }
    if (@(Get-Process -Name 'PIMELauncher' -ErrorAction SilentlyContinue).Count -gt 0) {
        return $true
    }
    Write-Note 'starting PIMELauncher...'
    try {
        Start-Process -FilePath (Join-Path $env:SystemRoot 'explorer.exe') -ArgumentList @(('"{0}"' -f $exe)) -ErrorAction SilentlyContinue | Out-Null
    } catch {
        # Non-fatal; the direct start below is the fallback.
    }
    for ($i = 0; $i -lt 32; $i++) {
        Start-Sleep -Milliseconds 250
        if (@(Get-Process -Name 'PIMELauncher' -ErrorAction SilentlyContinue).Count -gt 0) {
            Repair-PimeAppData
            return $true
        }
    }
    Write-Warn 'The shell-mediated start did not work; starting PIMELauncher directly (elevated).'
    try {
        Start-Process -FilePath $exe -ErrorAction SilentlyContinue | Out-Null
    } catch {
        return $false
    }
    for ($i = 0; $i -lt 32; $i++) {
        Start-Sleep -Milliseconds 250
        if (@(Get-Process -Name 'PIMELauncher' -ErrorAction SilentlyContinue).Count -gt 0) {
            Repair-PimeAppData
            return $true
        }
    }
    return $false
}

function Refresh-Ctfmon {
    $procs = @(Get-Process -Name 'ctfmon' -ErrorAction SilentlyContinue)
    if ($procs.Count -eq 0) { return }
    Write-Note 'restarting ctfmon so the new input method shows up immediately...'
    foreach ($proc in $procs) {
        try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch { }
    }
    Start-Sleep -Milliseconds 600
}

function Get-Bundle {
    param([string]$PayloadDir)
    $path = Join-Path $PayloadDir 'bundle.json'
    if (-not (Test-Path -LiteralPath $path)) {
        Fail ('Invalid payload: bundle.json not found in ' + $PayloadDir)
    }
    try {
        return (Get-Content -LiteralPath $path -Raw | ConvertFrom-Json)
    } catch {
        Fail ('Cannot parse bundle.json: ' + $_.Exception.Message)
    }
}

function Get-SupportDir {
    param([string]$Override)
    if ($Override) {
        return [IO.Path]::GetFullPath($Override)
    }
    return (Join-Path $env:ProgramData $script:SupportFolderName)
}

function Read-InstallMarker {
    param([string]$SupportDir)
    $path = Join-Path $SupportDir $script:MarkerName
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        return (Get-Content -LiteralPath $path -Raw | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Write-InstallMarker {
    param(
        [string]$SupportDir,
        [string]$Version,
        [string]$PimeVersion,
        [string]$Mode,
        [string]$InstallDir,
        [string]$PythonDir,
        [bool]$GramInstalled = $false
    )
    $marker = [ordered]@{
        product       = $script:ProductName
        version       = $Version
        pimeVersion   = $PimeVersion
        mode          = $Mode
        installDir    = $InstallDir
        pythonDir     = $PythonDir
        gramInstalled = $GramInstalled
        installedAt   = (Get-Date).ToString('o')
        installedBy   = 'cips-input installer'
    }
    $path = Join-Path $SupportDir $script:MarkerName
    $marker | ConvertTo-Json | Set-Content -LiteralPath $path -Encoding UTF8
}

function Remove-InstallMarker {
    param([string]$SupportDir)
    $path = Join-Path $SupportDir $script:MarkerName
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    }
}

function New-Shortcut {
    param(
        [string]$Path,
        [string]$TargetPath,
        [string]$Arguments,
        [string]$WorkingDirectory,
        [string]$IconLocation
    )
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $TargetPath
    if ($Arguments) { $shortcut.Arguments = $Arguments }
    if ($WorkingDirectory) { $shortcut.WorkingDirectory = $WorkingDirectory }
    if ($IconLocation) { $shortcut.IconLocation = $IconLocation }
    $shortcut.Save()
}

function Get-StartMenuFolder {
    $common = [Environment]::GetFolderPath([Environment+SpecialFolder]::CommonPrograms)
    return (Join-Path $common $script:ProductName)
}

function New-StartMenuEntries {
    param(
        [string]$SupportDir,
        [string]$InstallDir
    )
    $folder = Get-StartMenuFolder
    if (Test-Path -LiteralPath $folder) {
        Remove-Item -LiteralPath $folder -Recurse -Force -ErrorAction SilentlyContinue
    }
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    try {
        $uninstaller = Join-Path $SupportDir 'uninstall.ps1'
        New-Shortcut -Path (Join-Path $folder 'Uninstall cips-input.lnk') `
            -TargetPath (Get-PowerShellExe) `
            -Arguments ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $uninstaller) `
            -WorkingDirectory $SupportDir
        $launcher = Join-Path $InstallDir 'PIMELauncher.exe'
        if (Test-Path -LiteralPath $launcher) {
            New-Shortcut -Path (Join-Path $folder 'Start input method service.lnk') `
                -TargetPath $launcher `
                -WorkingDirectory $InstallDir
        }
    } catch {
        Write-Warn ('Could not create Start Menu shortcuts: ' + $_.Exception.Message)
    }
}

function Remove-StartMenuEntries {
    $folder = Get-StartMenuFolder
    if (Test-Path -LiteralPath $folder) {
        Remove-Item -LiteralPath $folder -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Get-UninstallRegistryKey {
    return 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\cips-input'
}

function Write-UninstallEntry {
    param(
        [string]$SupportDir,
        [string]$InstallDir,
        [string]$Mode,
        [string]$Version,
        [string]$ModuleDir
    )
    $key = Get-UninstallRegistryKey
    New-Item -Path $key -Force | Out-Null
    $uninstaller = Join-Path $SupportDir 'uninstall.ps1'
    $command = ('"{0}" -NoProfile -ExecutionPolicy Bypass -File "{1}"' -f (Get-PowerShellExe), $uninstaller)
    $display = 'cips-input'
    if ($Mode -eq 'full') { $display = 'cips-input (with PIME runtime)' }
    Set-ItemProperty -Path $key -Name DisplayName -Value $display
    Set-ItemProperty -Path $key -Name DisplayVersion -Value $Version
    Set-ItemProperty -Path $key -Name Publisher -Value 'cips-input'
    Set-ItemProperty -Path $key -Name InstallLocation -Value $InstallDir
    Set-ItemProperty -Path $key -Name UninstallString -Value $command
    Set-ItemProperty -Path $key -Name QuietUninstallString -Value ($command + ' -NoPrompt -Force')
    Set-ItemProperty -Path $key -Name NoModify -Value 1 -Type DWord
    Set-ItemProperty -Path $key -Name NoRepair -Value 1 -Type DWord
    $icon = Join-Path $ModuleDir 'icon.ico'
    if (Test-Path -LiteralPath $icon) {
        Set-ItemProperty -Path $key -Name DisplayIcon -Value $icon
    }
    $size = 0
    foreach ($relative in @('python', 'x64', 'x86', 'arm64')) {
        $path = Join-Path $InstallDir $relative
        if (Test-Path -LiteralPath $path) {
            $size += @(Get-ChildItem -LiteralPath $path -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
        }
    }
    if ($size -gt 0) {
        Set-ItemProperty -Path $key -Name EstimatedSize -Value ([int]($size / 1024)) -Type DWord
    }
}

function Remove-UninstallEntry {
    $key = Get-UninstallRegistryKey
    if (Test-Path -LiteralPath $key) {
        Remove-Item -LiteralPath $key -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Removes only registry values that this installer owns, and only when they
# still point at the installation being removed.
function Remove-OwnedRegistryValues {
    param([string]$InstallDir)
    $runKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run'
    try {
        $current = (Get-ItemProperty -Path $runKey -Name 'PIMELauncher' -ErrorAction SilentlyContinue).PIMELauncher
        if ($current -and ($current -replace '"', '').Trim() -ieq (Join-Path $InstallDir 'PIMELauncher.exe')) {
            Remove-ItemProperty -Path $runKey -Name 'PIMELauncher' -ErrorAction SilentlyContinue
        }
    } catch { }
    try {
        $pime = Get-Item 'HKLM:\SOFTWARE\PIME' -ErrorAction SilentlyContinue
        if ($pime) {
            $value = [string]$pime.GetValue('')
            if ($value -and ([IO.Path]::GetFullPath($value) -ieq [IO.Path]::GetFullPath($InstallDir))) {
                Remove-Item -LiteralPath 'HKLM:\SOFTWARE\PIME' -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    } catch { }
}

function Get-PendingReboot {
    # A broad pending-reboot probe used for diagnostics only. PIME's own
    # installer reacts to PendingFileRenameOperations entries for its folder by
    # rebooting even in silent mode, which is why Test-PimeDirPendingDelete
    # below checks the specific folder before the bundled setup is started.
    $keys = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending',
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'
    )
    foreach ($key in $keys) {
        if (Test-Path -LiteralPath $key) { return $true }
    }
    try {
        $session = (Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name 'PendingFileRenameOperations' -ErrorAction SilentlyContinue).PendingFileRenameOperations
        if ($session) { return $true }
    } catch { }
    return $false
}

# True when Windows has scheduled a delete/rename inside the PIME folder itself.
# The official PIME installer answers its own "pending reboot" prompt with YES
# when running silently and then reboots the machine, so the bundle must refuse
# to run it in that state.
function Test-PimeDirPendingDelete {
    param([string]$Dir)
    if (-not $Dir) { return $false }
    try {
        $value = (Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name 'PendingFileRenameOperations' -ErrorAction SilentlyContinue).PendingFileRenameOperations
    } catch {
        return $false
    }
    if (-not $value) { return $false }
    $needle = $Dir.TrimEnd('\')
    foreach ($entry in @($value)) {
        if ($entry -and $entry.IndexOf($needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $true
        }
    }
    return $false
}
