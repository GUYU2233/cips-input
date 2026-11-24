# cips-input uninstaller.
#
# Copied to %ProgramData%\cips-input by install.ps1 and registered as the
# "Uninstall" command in Apps & features. Two modes, taken from install.json:
#
#   full   - PIME was installed by the cips-input bundle: run PIME's own
#            uninstaller, then clean up the cips-input support files.
#   module - cips-input was added to an existing PIME installation: remove
#            only the module, re-register PIME so the language profile
#            disappears, and restart the service.
#
# ASCII only: Windows PowerShell 5.1 decodes BOM-less files with the ANSI code
# page, so non-ASCII text here would be garbled on some systems.

[CmdletBinding()]
param(
    [switch]$Elevated,
    [switch]$DryRun,
    [switch]$Force,
    [switch]$NoPrompt,
    [switch]$NoRegister,
    [switch]$NoLaunch,
    [switch]$NoElevate,
    [string]$SupportDir,
    [string]$InstallDir
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'common.ps1')

$script:LogPath = Join-Path $env:TEMP 'cips-input-uninstall.log'

function Remove-SupportFolder {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    try {
        Remove-Item -LiteralPath $Path -Recurse -Force
        return $true
    } catch {
        Write-Warn ('Could not remove {0}: {1}' -f $Path, $_.Exception.Message)
        return $false
    }
}

function Uninstall-FullRuntime {
    param(
        [string]$PimeDir,
        [string]$SupportDir
    )
    $uninstaller = Join-Path $PimeDir 'Uninstall.exe'
    if (Test-Path -LiteralPath $uninstaller) {
        Write-Step 'Running the PIME uninstaller'
        $proc = Start-Process -FilePath $uninstaller -ArgumentList '/S' -Wait -PassThru
        if ($proc.ExitCode -ne 0) {
            Write-Warn ('The PIME uninstaller returned exit code {0}.' -f $proc.ExitCode)
        }
        Start-Sleep -Milliseconds 500
    } else {
        Write-Warn 'PIME uninstaller not found; cleaning up the cips-input parts only.'
    }
    if (Test-Path -LiteralPath $PimeDir) {
        Write-Warn ('The PIME folder still exists: ' + $PimeDir)
        Write-Warn 'Close any application that is using the input method, or remove the folder manually.'
    }
    Remove-OwnedRegistryValues -InstallDir $PimeDir
}

function Uninstall-Module {
    param(
        [string]$PimeDir,
        [string]$PythonDir
    )
    $moduleDir = Join-Path (Join-Path $PythonDir 'input_methods') 'cips'
    if (Test-Path -LiteralPath $moduleDir) {
        Remove-Item -LiteralPath $moduleDir -Recurse -Force
        Write-Note ('removed ' + $moduleDir)
    } else {
        Write-Warn ('Module folder not found (already removed?): ' + $moduleDir)
    }
    if (-not $NoRegister) {
        Write-Step 'Re-registering PIME without cips-input'
        Register-TextService -Dir $PimeDir
    }
    if (-not $NoLaunch) {
        Write-Step 'Restarting the input method service'
        [void](Start-PimeLauncher -Dir $PimeDir)
    }
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

$exitCode = 0
try {
    Remove-Item -LiteralPath $script:LogPath -Force -ErrorAction SilentlyContinue
    Write-Log 'uninstaller started' 'STEP'

    $support = $PSScriptRoot
    if ($SupportDir) { $support = [IO.Path]::GetFullPath($SupportDir) }
    $marker = Read-InstallMarker -SupportDir $support
    if (-not $marker) {
        Fail ('install.json not found in ' + $support + '; this is not a cips-input installation.')
    }
    if ([string]$marker.product -ne $script:ProductName) {
        Fail 'install.json does not belong to cips-input.'
    }
    $mode = [string]$marker.mode
    if ($mode -ne 'full' -and $mode -ne 'module') {
        Fail ('Unknown installation mode in install.json: ' + $mode)
    }
    $pimeDir = [string]$marker.installDir
    if ($InstallDir) { $pimeDir = [IO.Path]::GetFullPath($InstallDir) }
    if (-not $pimeDir) {
        Fail 'install.json does not record an install folder.'
    }
    $pythonDir = [string]$marker.pythonDir
    if (-not $pythonDir) { $pythonDir = (Join-Path $pimeDir 'python') }

    Write-Note ('mode: {0}' -f $mode)
    Write-Note ('PIME folder: {0}' -f $pimeDir)
    Write-Note ('support folder: {0}' -f $support)

    if (-not (Test-Administrator) -and -not $Elevated) {
        if ($DryRun -or $NoElevate) {
            Write-Warn 'Running without administrator rights; privileged steps will be skipped.'
        } else {
            $extra = @()
            foreach ($name in @('DryRun', 'Force', 'NoPrompt', 'NoRegister', 'NoLaunch')) {
                if ((Get-Variable -Name $name -ValueOnly)) { $extra += ('-' + $name) }
            }
            if ($SupportDir) { $extra += @('-SupportDir', ('"{0}"' -f $SupportDir)) }
            if ($InstallDir) { $extra += @('-InstallDir', ('"{0}"' -f $InstallDir)) }
            $childCode = Invoke-SelfElevation -ScriptPath $PSCommandPath -ExtraArgs $extra
            exit $childCode
        }
    }

    if ($DryRun) {
        Write-Step 'Dry run: no changes will be made'
        if ($mode -eq 'full') {
            Write-Note 'would run the PIME uninstaller and remove all bundled files'
        } else {
            Write-Note ('would remove the module under ' + $pythonDir)
            Write-Note 'would re-register PIME and restart the service'
        }
        Write-Note 'would remove the uninstall entry, Start Menu folder and support files'
        exit 0
    }

    if (-not $Force -and -not $NoPrompt) {
        $question = 'Remove cips-input from this machine?'
        if ($mode -eq 'full') { $question = 'Remove cips-input AND the bundled PIME runtime from this machine?' }
        $answer = Read-Host ($question + ' [y/N]')
        if ($answer -notmatch '^(y|yes)$') {
            Write-Host 'Cancelled.'
            exit 0
        }
    }

    Stop-PimeLauncher -Dir $pimeDir
    if (-not $NoRegister) {
        Write-Step 'Unregistering the text service'
        Unregister-TextService -Dir $pimeDir
    }

    if ($mode -eq 'full') {
        Uninstall-FullRuntime -PimeDir $pimeDir -SupportDir $support
    } else {
        Uninstall-Module -PimeDir $pimeDir -PythonDir $pythonDir
    }

    Write-Step 'Cleaning up'
    Remove-UninstallEntry
    Remove-StartMenuEntries
    Refresh-Ctfmon
    if (-not (Test-Path -LiteralPath $pimeDir)) {
        # PIME folder is gone; nothing else to clean there.
    }
    [void](Remove-SupportFolder -Path $support)

    Write-Host ''
    Write-Host '  cips-input has been removed.' -ForegroundColor Green
    Write-Host ('  Log: {0}' -f $script:LogPath)
    Write-Log 'uninstallation finished' 'STEP'
} catch {
    $message = $_.Exception.Message
    Write-Host ''
    Write-Host ('Uninstallation failed: ' + $message) -ForegroundColor Red
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
