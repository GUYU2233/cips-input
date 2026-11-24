# cips-input one-click installer payload.
#
# Runs as administrator (the bootstrapper carries a requireAdministrator
# manifest; when invoked directly the script self-elevates). Modes:
#
#   full   - no PIME runtime found: run the bundled official PIME installer
#            silently, then deploy the cips-input module into it.
#   module - a PIME runtime already exists: only add/update the cips-input
#            module and re-register the text service.
#
# The permanent support folder (%ProgramData%\cips-input) holds the uninstaller,
# the install marker and the licensing notices, so the PIME folder stays clean.
#
# ASCII only: Windows PowerShell 5.1 decodes BOM-less files with the ANSI code
# page, so non-ASCII text here would be garbled on some systems.

[CmdletBinding()]
param(
    [switch]$Elevated,
    [switch]$DryRun,
    [switch]$Force,
    [switch]$NoRegister,
    [switch]$NoLaunch,
    [switch]$NoPrompt,
    [switch]$NoElevate,
    [string]$InstallDir,
    [string]$SupportDir,
    [string]$PayloadDir
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'common.ps1')

function Get-PlannedMode {
    param(
        [string]$PimeDir,
        [string]$SupportDir,
        [bool]$DryRun
    )
    $runtime = Test-PimeRuntime -Dir $PimeDir
    $marker = Read-InstallMarker -SupportDir $SupportDir
    $ourFull = $false
    if ($marker -and ([string]$marker.product -eq $script:ProductName) -and ([string]$marker.mode -eq 'full')) {
        $ourFull = $true
    }
    if ($runtime) {
        if ($ourFull) { return 'full' }
        return 'module'
    }
    return 'full'
}

function Deploy-CipsModule {
    param(
        [string]$PayloadDir,
        [string]$PythonDir
    )
    $source = Join-Path $PayloadDir 'module'
    if (-not (Test-Path -LiteralPath (Join-Path $source 'ime.json'))) {
        Fail 'Invalid payload: module/ime.json is missing.'
    }
    $inputMethods = Join-Path $PythonDir 'input_methods'
    if (-not (Test-Path -LiteralPath $inputMethods)) {
        New-Item -ItemType Directory -Path $inputMethods -Force | Out-Null
    }
    $target = Join-Path $inputMethods 'cips'
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
    Copy-Item -LiteralPath $source -Destination $target -Recurse -Force
    # The dev tree may contain byte-code caches; never ship them to the target.
    foreach ($cache in @(Get-ChildItem -LiteralPath $target -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $cache.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Note ('module deployed to ' + $target)
    return $target
}

function Install-PimeRuntime {
    param(
        [string]$PayloadDir,
        [string]$PimeDir,
        [string]$SetupName,
        [string]$ExpectedSha256
    )
    $setup = Join-Path $PayloadDir $SetupName
    if (-not (Test-Path -LiteralPath $setup)) {
        Fail ('Bundled PIME installer not found: ' + $setup)
    }
    Write-Step 'Verifying the bundled PIME installer'
    $actual = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLower()
    if ($ExpectedSha256 -and ($actual -ne $ExpectedSha256.ToLower())) {
        Fail ('PIME installer checksum mismatch: expected {0}, got {1}' -f $ExpectedSha256, $actual)
    }
    Write-Note ('sha256 ' + $actual)

    Write-Step 'Running the bundled PIME installer (silent; this can take a few minutes)'
    $proc = Start-Process -FilePath $setup -ArgumentList '/S' -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        Fail ('The PIME installer failed with exit code {0}.' -f $proc.ExitCode)
    }
    if (-not (Test-PimeRuntime -Dir $PimeDir)) {
        Fail ('The PIME installer finished, but no PIME runtime was found in ' + $PimeDir)
    }
    Write-Note 'PIME runtime installed'
}

function Write-SupportFiles {
    param(
        [string]$PayloadDir,
        [string]$SupportDir,
        [string]$Version,
        [string]$PimeVersion,
        [string]$Mode,
        [string]$InstallDir,
        [string]$PythonDir
    )
    if (Test-Path -LiteralPath $SupportDir) {
        Remove-Item -LiteralPath $SupportDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $SupportDir -Force | Out-Null
    foreach ($name in @('uninstall.ps1', 'common.ps1', 'THIRD_PARTY_NOTICES.md')) {
        $source = Join-Path $PayloadDir $name
        if (Test-Path -LiteralPath $source) {
            Copy-Item -LiteralPath $source -Destination (Join-Path $SupportDir $name) -Force
        }
    }
    $licenses = Join-Path $PayloadDir 'licenses'
    if (Test-Path -LiteralPath $licenses) {
        Copy-Item -LiteralPath $licenses -Destination (Join-Path $SupportDir 'licenses') -Recurse -Force
    }
    Write-InstallMarker -SupportDir $SupportDir -Version $Version -PimeVersion $PimeVersion `
        -Mode $Mode -InstallDir $InstallDir -PythonDir $PythonDir
}

function Write-Summary {
    param(
        [string]$Mode,
        [string]$PimeDir,
        [string]$Version,
        [int]$Launched
    )
    Write-Host ''
    Write-Host ('  cips-input {0} installed successfully (mode: {1})' -f $Version, $Mode) -ForegroundColor Green
    Write-Host ('  PIME runtime: {0}' -f $PimeDir)
    if ($Launched -eq 0) {
        Write-Warn 'PIMELauncher could not be started. Sign out and sign in, or start it from the Start Menu.'
    }
    Write-Host '  Next: press Win+Space and choose "cips-input".'
    Write-Host '        If it is not listed yet, sign out and sign in once.'
    Write-Host ('  Log:  {0}' -f $script:LogPath)
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

$exitCode = 0
try {
    Remove-Item -LiteralPath $script:LogPath -Force -ErrorAction SilentlyContinue
    Write-Log ('installer started: pid={0}, elevated={1}' -f $PID, (Test-Administrator)) 'STEP'

    if (-not (Test-Administrator) -and -not $Elevated) {
        if ($DryRun -or $NoRegister -or $NoElevate) {
            Write-Warn 'Running without administrator rights; privileged steps will be skipped.'
        } else {
            $extra = @()
            foreach ($name in @('DryRun', 'Force', 'NoRegister', 'NoLaunch', 'NoPrompt')) {
                if ((Get-Variable -Name $name -ValueOnly)) { $extra += ('-' + $name) }
            }
            if ($InstallDir) { $extra += @('-InstallDir', ('"{0}"' -f $InstallDir)) }
            if ($SupportDir) { $extra += @('-SupportDir', ('"{0}"' -f $SupportDir)) }
            if ($PayloadDir) { $extra += @('-PayloadDir', ('"{0}"' -f $PayloadDir)) }
            $childCode = Invoke-SelfElevation -ScriptPath $PSCommandPath -ExtraArgs $extra
            exit $childCode
        }
    }

    Write-Step 'Checking prerequisites'
    if (-not (Test-Windows10)) {
        Fail 'Windows 10 or later is required.'
    }
    if (-not [Environment]::Is64BitOperatingSystem) {
        Fail 'A 64-bit Windows installation is required.'
    }
    $arch = Get-WindowsArch
    Write-Note ('windows build {0}, architecture {1}' -f [Environment]::OSVersion.Version.Build, $arch)
    if (Get-PendingReboot) {
        Write-Warn 'Windows reports a pending reboot; consider restarting after the installation.'
    }

    $payload = $PSScriptRoot
    if ($PayloadDir) { $payload = [IO.Path]::GetFullPath($PayloadDir) }
    $bundle = Get-Bundle -PayloadDir $payload
    Write-Note ('bundle {0}, bundled PIME {1}' -f $bundle.version, $bundle.pimeVersion)

    $pimeDir = Get-PimeInstallDir -Override $InstallDir
    $support = Get-SupportDir -Override $SupportDir
    $mode = Get-PlannedMode -PimeDir $pimeDir -SupportDir $support -DryRun:$DryRun
    $needSetup = (-not (Test-PimeRuntime -Dir $pimeDir)) -and ($mode -eq 'full')
    if (-not (Test-PimeRuntime -Dir $pimeDir) -and $mode -eq 'module') {
        Fail ('Internal error: module mode selected without a PIME runtime in ' + $pimeDir)
    }
    if ($mode -eq 'full' -and $InstallDir) {
        Fail '-InstallDir can only be used to update an existing PIME installation.'
    }

    Write-Note ('mode: {0}' -f $mode)
    Write-Note ('PIME folder: {0}' -f $pimeDir)
    Write-Note ('support folder: {0}' -f $support)

    if ($DryRun) {
        Write-Step 'Dry run: no changes will be made'
        Write-Note ('bundle payload: ' + $payload)
        Write-Note ('would run bundled PIME installer: ' + $needSetup)
        Write-Note ('would deploy module from: ' + (Join-Path $payload 'module'))
        Write-Note ('would write uninstall entry + Start Menu shortcuts: ' + (-not $NoRegister))
        Write-Note ('would start PIMELauncher: ' + (-not $NoLaunch))
        Write-Log 'dry run finished' 'STEP'
        exit 0
    }

    if (Test-PimeDirPendingDelete -Dir $pimeDir) {
        # The official PIME installer answers its pending-reboot prompt with YES
        # in silent mode and then reboots the machine. Refuse instead.
        Fail ('Windows has scheduled maintenance for {0} at the next restart. Restart Windows, then run this installer again.' -f $pimeDir)
    }

    if ($needSetup) {
        Install-PimeRuntime -PayloadDir $payload -PimeDir $pimeDir `
            -SetupName ([string]$bundle.pimeSetup) -ExpectedSha256 ([string]$bundle.pimeSetupSha256)
    }

    Write-Step 'Deploying the cips-input module'
    $pythonDir = Get-PythonBackendDir -Dir $pimeDir
    Stop-PimeLauncher -Dir $pimeDir
    $moduleDir = Deploy-CipsModule -PayloadDir $payload -PythonDir $pythonDir

    if (-not $NoRegister) {
        Write-Step 'Registering the text service'
        Register-TextService -Dir $pimeDir
    } else {
        Write-Warn 'Skipping text service registration (-NoRegister).'
    }

    Write-Step 'Writing installation data'
    Write-SupportFiles -PayloadDir $payload -SupportDir $support -Version ([string]$bundle.version) `
        -PimeVersion ([string]$bundle.pimeVersion) -Mode $mode -InstallDir $pimeDir -PythonDir $pythonDir

    if (-not $NoRegister) {
        Write-UninstallEntry -SupportDir $support -InstallDir $pimeDir -Mode $mode `
            -Version ([string]$bundle.version) -ModuleDir $moduleDir
        New-StartMenuEntries -SupportDir $support -InstallDir $pimeDir
        Refresh-Ctfmon
    } else {
        Write-Warn 'Skipping uninstall entry and Start Menu shortcuts (-NoRegister).'
    }

    $launched = 1
    if (-not $NoLaunch) {
        Write-Step 'Starting the input method service'
        if (-not (Start-PimeLauncher -Dir $pimeDir)) { $launched = 0 }
    }

    Write-Summary -Mode $mode -PimeDir $pimeDir -Version ([string]$bundle.version) -Launched $launched
    Write-Log 'installation finished' 'STEP'
} catch {
    $message = $_.Exception.Message
    Write-Host ''
    Write-Host ('Installation failed: ' + $message) -ForegroundColor Red
    Write-Host ('See the log for details: ' + $script:LogPath)
    Write-Log ('FAILED: ' + $message) 'FAIL'
    $exitCode = 1
}

if (-not $NoPrompt) {
    Write-Host ''
    Write-Host 'Press Enter to close this window.'
    [void](Read-Host)
}
exit $exitCode
