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
# Optional language model:
#   -InstallGram          Install wanxiang-lts-zh-hans.gram (~373 MB) and
#                         configure rime_frost_with_gram patches.
#   -GramPath <path>      Use a local .gram file instead of downloading it.
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
    [switch]$InstallGram,
    [string]$GramPath,
    [string]$InstallDir,
    [string]$SupportDir,
    [string]$PayloadDir
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'common.ps1')

$script:GramPatchSchemas = @(
    'rime_frost',
    'rime_frost_double_pinyin',
    'rime_frost_double_pinyin_flypy',
    'rime_frost_double_pinyin_mspy',
    'rime_frost_double_pinyin_sogou',
    'rime_frost_double_pinyin_abc',
    'rime_frost_double_pinyin_ziguang'
)

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
        [string]$PythonDir,
        [switch]$PreserveExistingGram,
        [string]$ExpectedGramSha256
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

    # Keep an already-downloaded 373 MB gram file across upgrades so we do not
    # re-download it unnecessarily when -InstallGram is requested.
    $preservedGram = $null
    if ($PreserveExistingGram) {
        $existingGram = Join-Path $target 'rime_data\wanxiang-lts-zh-hans.gram'
        if (Test-Path -LiteralPath $existingGram) {
            $ok = $true
            if ($ExpectedGramSha256) {
                $hash = Get-Sha256Hex -LiteralPath $existingGram
                $ok = ($hash -eq $ExpectedGramSha256.ToLower())
            }
            if ($ok) {
                $preservedGram = Join-Path $env:TEMP ('cips-preserved-' + [Guid]::NewGuid().ToString('N') + '.gram')
                Move-Item -LiteralPath $existingGram -Destination $preservedGram -Force
                Write-Note 'preserved existing wanxiang-lts-zh-hans.gram across upgrade'
            }
        }
    }

    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
    Copy-Item -LiteralPath $source -Destination $target -Recurse -Force
    foreach ($cache in @(Get-ChildItem -LiteralPath $target -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $cache.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($preservedGram -and (Test-Path -LiteralPath $preservedGram)) {
        $destGramDir = Join-Path $target 'rime_data'
        if (-not (Test-Path -LiteralPath $destGramDir)) {
            New-Item -ItemType Directory -Path $destGramDir -Force | Out-Null
        }
        Move-Item -LiteralPath $preservedGram -Destination (Join-Path $destGramDir 'wanxiang-lts-zh-hans.gram') -Force
    }
    Write-Note ('module deployed to ' + $target)
    return $target
}

function Install-WanxiangGram {
    param(
        [string]$ModuleDir,
        [object]$Bundle,
        [string]$LocalGramPath
    )
    $dataDir = Join-Path $ModuleDir 'rime_data'
    $runtimeDir = Join-Path $ModuleDir 'rime_runtime'
    if (-not (Test-Path -LiteralPath $dataDir)) {
        Fail ('Cannot install Wanxiang gram: rime_data directory is missing under ' + $ModuleDir)
    }

    $gramName = if ($Bundle.gramFile) { [string]$Bundle.gramFile } else { 'wanxiang-lts-zh-hans.gram' }
    $gramUrl = [string]$Bundle.gramUrl
    $gramSha256 = [string]$Bundle.gramSha256
    $gramSize = [long]$Bundle.gramSize
    $destGram = Join-Path $dataDir $gramName

    Write-Step ('Installing optional Wanxiang language model (' + $gramName + ')')
    if ($LocalGramPath) {
        $resolved = [IO.Path]::GetFullPath($LocalGramPath)
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            Fail ('Specified -GramPath does not exist: ' + $resolved)
        }
        $localLen = (Get-Item -LiteralPath $resolved).Length
        if ($localLen -le 0) {
            Fail ('Specified -GramPath is empty: ' + $resolved)
        }
        Write-Note ('copying local gram model: ' + $resolved)
        Copy-Item -LiteralPath $resolved -Destination $destGram -Force
        $actualHash = Get-Sha256Hex -LiteralPath $destGram
        if ($gramSha256 -and ($actualHash -ne $gramSha256.ToLower())) {
            Write-Warn ('Local gram SHA-256 ({0}) differs from pinned LTS ({1}); using user-supplied file.' -f $actualHash, $gramSha256)
        } else {
            Write-Note ('verified sha256 ' + $actualHash)
        }
    } elseif (Test-Path -LiteralPath $destGram) {
        $actualHash = Get-Sha256Hex -LiteralPath $destGram
        if ($gramSha256 -and ($actualHash -ne $gramSha256.ToLower())) {
            Fail ('Existing gram checksum mismatch: ' + $actualHash)
        }
        Write-Note ('verified existing gram sha256 ' + $actualHash)
    } else {
        if (-not $gramUrl) {
            Fail 'bundle.json does not specify gramUrl and no -GramPath was provided.'
        }
        $partPath = $destGram + '.part'
        Remove-Item -LiteralPath $partPath -Force -ErrorAction SilentlyContinue
        Write-Note ('downloading ' + $gramUrl + ' (~373 MB)...')
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        } catch { }
        try {
            $ProgressPreference = 'SilentlyContinue'
            Invoke-WebRequest -Uri $gramUrl -OutFile $partPath -TimeoutSec 1800
            $dlLen = (Get-Item -LiteralPath $partPath).Length
            if ($gramSize -gt 0 -and $dlLen -ne $gramSize) {
                Fail ('Downloaded gram size mismatch: expected {0}, got {1}' -f $gramSize, $dlLen)
            }
            $dlHash = Get-Sha256Hex -LiteralPath $partPath
            if ($gramSha256 -and ($dlHash -ne $gramSha256.ToLower())) {
                Fail ('Downloaded gram SHA-256 mismatch: expected {0}, got {1}' -f $gramSha256, $dlHash)
            }
            Move-Item -LiteralPath $partPath -Destination $destGram -Force
            Write-Note ('verified downloaded gram sha256 ' + $dlHash)
        } catch {
            Remove-Item -LiteralPath $partPath -Force -ErrorAction SilentlyContinue
            throw
        }
    }

    $patchTemplate = Join-Path $ModuleDir 'rime_config\gram_patch.yaml'
    if (-not (Test-Path -LiteralPath $patchTemplate)) {
        Fail ('gram_patch.yaml template missing: ' + $patchTemplate)
    }
    foreach ($schema in $script:GramPatchSchemas) {
        $customTarget = Join-Path $dataDir ($schema + '.custom.yaml')
        Copy-Item -LiteralPath $patchTemplate -Destination $customTarget -Force
    }
    Write-Note 'applied rime_frost_with_gram schema patches'

    $deployer = Join-Path $runtimeDir 'rime_deployer.exe'
    if (Test-Path -LiteralPath $deployer) {
        Write-Note 'recompiling Rime schemas with Wanxiang gram config...'
        $buildDir = Join-Path $dataDir 'build'
        $oldPath = $env:PATH
        try {
            $env:PATH = $runtimeDir + ';' + $oldPath
            $proc = Start-Process -FilePath $deployer `
                -ArgumentList @('--build', ('"{0}"' -f $dataDir), ('"{0}"' -f $dataDir), ('"{0}"' -f $buildDir)) `
                -Wait -PassThru -NoNewWindow
            if ($proc.ExitCode -ne 0) {
                Write-Warn ('rime_deployer.exe returned exit code {0}; schemas will compile on first use.' -f $proc.ExitCode)
            }
        } finally {
            $env:PATH = $oldPath
        }
        foreach ($ephemeral in @('user.yaml', 'installation.yaml')) {
            $epPath = Join-Path $dataDir $ephemeral
            if (Test-Path -LiteralPath $epPath) {
                Remove-Item -LiteralPath $epPath -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

function Remove-BundledPimeImes {
    param([string]$PimeDir)

    $removedAny = $false
    $backendRoots = @(
        (Join-Path $PimeDir 'python\input_methods'),
        (Join-Path $PimeDir 'node\input_methods'),
        (Join-Path $PimeDir 'go-backend\input_methods')
    )
    $toDelete = New-Object System.Collections.Generic.List[string]
    foreach ($root in $backendRoots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        foreach ($dir in @(Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue)) {
            if ($dir.Name -in @('cips', '__pycache__')) { continue }
            $toDelete.Add($dir.FullName)
        }
    }

    if ($toDelete.Count -gt 0) {
        Write-Step 'Removing bundled third-party PIME input methods (keeping cips-input only)'
        try {
            Unregister-TextService -Dir $PimeDir
        } catch {
            Write-Warn ('Could not pre-unregister PIME text service before cleanup: ' + $_.Exception.Message)
        }
        foreach ($path in $toDelete) {
            Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue
            Write-Note ('removed ' + $path)
            $removedAny = $true
        }
    }

    foreach ($smBase in @(
        (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'),
        (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs')
    )) {
        if (-not (Test-Path -LiteralPath $smBase)) { continue }
        foreach ($folder in @(Get-ChildItem -LiteralPath $smBase -Directory -Filter 'PIME*' -ErrorAction SilentlyContinue)) {
            foreach ($lnk in @(Get-ChildItem -LiteralPath $folder.FullName -Filter '*.lnk' -ErrorAction SilentlyContinue)) {
                try {
                    $shell = New-Object -ComObject WScript.Shell
                    $sc = $shell.CreateShortcut($lnk.FullName)
                    if ($sc.Arguments -match 'chewing' -or $sc.TargetPath -match 'chewing') {
                        Remove-Item -LiteralPath $lnk.FullName -Force -ErrorAction SilentlyContinue
                    }
                } catch { }
            }
        }
    }

    return $removedAny
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
    $actual = Get-Sha256Hex -LiteralPath $setup
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
        [string]$PythonDir,
        [bool]$GramInstalled = $false
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
        -Mode $Mode -InstallDir $InstallDir -PythonDir $PythonDir -GramInstalled $GramInstalled
}

function Write-Summary {
    param(
        [string]$Mode,
        [string]$PimeDir,
        [string]$Version,
        [int]$Launched,
        [bool]$GramInstalled
    )
    Write-Host ''
    Write-Host ('  cips-input {0} installed successfully (mode: {1}, gram: {2})' -f $Version, $Mode, $GramInstalled) -ForegroundColor Green
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
            foreach ($name in @('DryRun', 'Force', 'NoRegister', 'NoLaunch', 'NoPrompt', 'InstallGram')) {
                if ((Get-Variable -Name $name -ValueOnly)) { $extra += ('-' + $name) }
            }
            if ($GramPath) { $extra += @('-GramPath', ('"{0}"' -f $GramPath)) }
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

    $wantGram = [bool]$InstallGram -or (-not [string]::IsNullOrWhiteSpace($GramPath))

    Write-Note ('mode: {0}' -f $mode)
    Write-Note ('PIME folder: {0}' -f $pimeDir)
    Write-Note ('support folder: {0}' -f $support)
    Write-Note ('install Wanxiang gram: {0}' -f $wantGram)

    if ($DryRun) {
        Write-Step 'Dry run: no changes will be made'
        Write-Note ('bundle payload: ' + $payload)
        Write-Note ('would run bundled PIME installer: ' + $needSetup)
        Write-Note ('would deploy module from: ' + (Join-Path $payload 'module'))
        Write-Note ('would install wanxiang gram model: ' + $wantGram)
        Write-Note ('would write uninstall entry + Start Menu shortcuts: ' + (-not $NoRegister))
        Write-Note ('would start PIMELauncher: ' + (-not $NoLaunch))
        Write-Log 'dry run finished' 'STEP'
        exit 0
    }

    if (Test-PimeDirPendingDelete -Dir $pimeDir) {
        Fail ('Windows has scheduled maintenance for {0} at the next restart. Restart Windows, then run this installer again.' -f $pimeDir)
    }

    if ($needSetup) {
        Install-PimeRuntime -PayloadDir $payload -PimeDir $pimeDir `
            -SetupName ([string]$bundle.pimeSetup) -ExpectedSha256 ([string]$bundle.pimeSetupSha256)
    }

    Write-Step 'Deploying the cips-input module'
    $pythonDir = Get-PythonBackendDir -Dir $pimeDir
    Stop-PimeLauncher -Dir $pimeDir
    if ($mode -eq 'full') {
        [void](Remove-BundledPimeImes -PimeDir $pimeDir)
    }
    $preserveGram = $wantGram -and [string]::IsNullOrWhiteSpace($GramPath)
    $moduleDir = Deploy-CipsModule -PayloadDir $payload -PythonDir $pythonDir `
        -PreserveExistingGram:$preserveGram -ExpectedGramSha256 ([string]$bundle.gramSha256)

    if ($wantGram) {
        Install-WanxiangGram -ModuleDir $moduleDir -Bundle $bundle -LocalGramPath $GramPath
    }

    if (-not $NoRegister) {
        Write-Step 'Registering the text service'
        Register-TextService -Dir $pimeDir
    } else {
        Write-Warn 'Skipping text service registration (-NoRegister).'
    }

    Write-Step 'Writing installation data'
    Write-SupportFiles -PayloadDir $payload -SupportDir $support -Version ([string]$bundle.version) `
        -PimeVersion ([string]$bundle.pimeVersion) -Mode $mode -InstallDir $pimeDir -PythonDir $pythonDir `
        -GramInstalled:$wantGram

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

    Write-Summary -Mode $mode -PimeDir $pimeDir -Version ([string]$bundle.version) -Launched $launched -GramInstalled:$wantGram
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
