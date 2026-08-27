# packaging/windows/package-cleanstreamts.ps1
<#
.SYNOPSIS
    Build CleanStreamTS into a single self-extracting installer exe.

.DESCRIPTION
    Runs PyInstaller against cleanstreamts.spec, then wraps dist\CleanStreamTS
    into ONE self-extracting installer: dist\CleanStreamTS-install.exe

    Structure follows ChitraMaya's packager, with one difference. ChitraMaya's
    payload is ~1.5 GB, so it has to ship as separate .7z volumes beside the
    exe and its installer verifies every volume is present. CleanStreamTS is
    small enough to live INSIDE the exe, so:

        CleanStreamTS-install.exe
          = 7zSD.sfx + sfx_config.txt + payload.7z

        payload.7z
          = install.cmd + install.ps1 + 7zr.exe + CleanStreamTS-app.7z

    The SFX unpacks that payload to a temp folder and runs install.cmd, which
    runs install.ps1, which asks where to install and extracts the app archive
    there with the bundled 7zr.exe. One file for the user to download, and
    none of the "you need all three files" support burden.

    If the payload ever grows past GitHub's 2 GB single-asset limit the build
    STOPS and says so rather than silently producing something unreleasable.

    Vendor files required in packaging\windows\vendor\ :
      7zSD.sfx    from the LZMA SDK  (7-zip.org/sdk.html -> bin\7zSD.sfx)
      7zr.exe     from the LZMA SDK  (7-zip.org/sdk.html -> bin\7zr.exe)
      ffmpeg\     LGPL ffmpeg.exe + ffprobe.exe (+ DLLs for a shared build)

    Modern 7-Zip "extra" packages no longer carry SFX modules; both vendor
    binaries come from the LZMA SDK bin\ folder.

.EXAMPLE
    powershell -ExecutionPolicy Bypass .\packaging\windows\package-cleanstreamts.ps1
#>

[CmdletBinding()]
param(
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$AppName     = 'CleanStreamTS'
$InstallBase = "$AppName-install"
$SpecDir     = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot    = (Resolve-Path (Join-Path $SpecDir '..\..')).Path
$VendorDir   = Join-Path $SpecDir 'vendor'
$InstSrcDir  = Join-Path $SpecDir 'installer'
$DistDir     = Join-Path $RepoRoot 'dist'
$BuildDir    = Join-Path $RepoRoot 'build'
$PayloadDir  = Join-Path $DistDir $AppName

$SfxModule = Join-Path $VendorDir '7zSD.sfx'
$SevenZr   = Join-Path $VendorDir '7zr.exe'

$GithubAssetLimitBytes = 2GB

function Write-Step { param($m) Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Write-Ok   { param($m) Write-Host "  $m" -ForegroundColor Green }
function Write-Note { param($m) Write-Host "  $m" -ForegroundColor Yellow }

# --------------------------------------------------------------------------
# Preflight - check EVERY input before spending minutes on a build.
# ChitraMaya Batch 24f: the installer scripts must be verified BEFORE the
# long archive step, or staging fails after the expensive job has run.
# --------------------------------------------------------------------------

Write-Step 'Preflight'

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    throw "pyinstaller not found. Install the build extra:  pip install -e "".[build]"""
}
Write-Ok 'pyinstaller present'

$version = & python -c "import sys; sys.path.insert(0, r'$RepoRoot'); import cleanstreamts; print(cleanstreamts.__version__)"
if (-not $version) { throw 'Could not read cleanstreamts.__version__' }
$version = $version.Trim()
Write-Ok "version $version"

$missing = @()
foreach ($f in @($SfxModule, $SevenZr)) {
    if (-not (Test-Path $f)) { $missing += $f }
}
foreach ($n in @('install.cmd', 'install.ps1', 'sfx_config.txt')) {
    $p = Join-Path $InstSrcDir $n
    if (-not (Test-Path $p)) { $missing += $p }
}
if ($missing.Count -gt 0) {
    Write-Host ''
    Write-Host 'Missing required packaging inputs:' -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    Write-Host ''
    Write-Host 'Vendor files: see packaging\windows\vendor\README.md' -ForegroundColor Yellow
    Write-Host 'Installer scripts ship in the repo under packaging\windows\installer\.' -ForegroundColor Yellow
    throw 'Preflight failed.'
}
Write-Ok 'sfx module, 7zr, installer scripts present'

if (Test-Path (Join-Path $VendorDir 'ffmpeg\ffmpeg.exe')) {
    Write-Ok 'bundled ffmpeg present'
} else {
    Write-Note 'no vendor\ffmpeg\ffmpeg.exe - the packaged app will need ffmpeg on PATH'
}

# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

if (-not $SkipBuild) {
    Write-Step 'PyInstaller'
    Push-Location $RepoRoot
    try {
        & pyinstaller (Join-Path $SpecDir 'cleanstreamts.spec') --noconfirm
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
    } finally {
        Pop-Location
    }
    Write-Ok 'build complete'
} else {
    Write-Note 'skipping build (-SkipBuild)'
}

if (-not (Test-Path $PayloadDir)) { throw "Payload folder not found: $PayloadDir" }

foreach ($exe in @("$AppName.exe", "$AppName-cli.exe")) {
    if (-not (Test-Path (Join-Path $PayloadDir $exe))) {
        throw "Expected executable missing from the build: $exe"
    }
    Write-Ok "found $exe"
}

$payloadBytes = (Get-ChildItem $PayloadDir -Recurse -File | Measure-Object Length -Sum).Sum
Write-Ok ("app folder {0:N1} MB" -f ($payloadBytes / 1MB))

# --------------------------------------------------------------------------
# Smoke-test the BUILT EXE before packaging it.
#
# This step exists because of a shipped failure: the spec bundled templates
# and static correctly, the spec's own manifest check confirmed they were
# present, and the app still died with TemplateNotFound on first launch -
# the code resolved the resource root one directory too high. Verifying that
# files are IN the bundle is not the same as verifying the app can FIND them.
#
# The only thing that catches that class of bug is running the packaged
# executable. So the packager runs it here, and a failure stops the build
# instead of reaching a user's clean machine.
# --------------------------------------------------------------------------

Write-Step 'Self-check (built exe)'

$cliExe = Join-Path $PayloadDir "$AppName-cli.exe"
& $cliExe self-check | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "Self-check FAILED on the built exe (exit $LASTEXITCODE). The bundle is broken - not packaging it. See the failures above."
}
Write-Ok 'built exe resolves its resources and tools'

# --------------------------------------------------------------------------
# Clear stale artifacts - LOUDLY.
# ChitraMaya Batch 24c: -ErrorAction SilentlyContinue here masks a locked
# file (antivirus scan) and 7-Zip then fails with a misleading error.
# --------------------------------------------------------------------------

Write-Step 'Clearing stale artifacts'

$appArchive = Join-Path $BuildDir "$AppName-app.7z"
$payload7z  = Join-Path $BuildDir 'installer_payload.7z'
$stageDir   = Join-Path $BuildDir 'installer_payload'
$installer  = Join-Path $DistDir "$InstallBase.exe"

foreach ($stale in @($appArchive, $payload7z, $installer)) {
    if (-not (Test-Path $stale)) { continue }
    $removed = $false
    for ($try = 1; $try -le 5; $try++) {
        try {
            Remove-Item $stale -Force
            $removed = $true
            Write-Ok "removed stale $(Split-Path -Leaf $stale)"
            break
        } catch {
            Write-Note "locked, retry $try/5: $(Split-Path -Leaf $stale)"
            Start-Sleep -Seconds 2
        }
    }
    if (-not $removed) {
        throw "Could not remove $stale - it is locked. Close anything using it and re-run."
    }
}
if (Test-Path $stageDir) { Remove-Item -Recurse -Force $stageDir }
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

# --------------------------------------------------------------------------
# App archive - what the user ends up with on disk
# --------------------------------------------------------------------------

Write-Step 'Compressing application'

Push-Location $DistDir
try {
    & $SevenZr a -t7z $appArchive $AppName -mx=7 -mmt=on | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "7zr failed compressing the app (exit $LASTEXITCODE)" }
} finally {
    Pop-Location
}
if (-not (Test-Path $appArchive)) { throw 'App archive was not produced.' }

$appArchiveBytes = (Get-Item $appArchive).Length
Write-Ok ("app archive {0:N1} MB" -f ($appArchiveBytes / 1MB))

if ($appArchiveBytes -ge $GithubAssetLimitBytes) {
    throw ("App archive is {0:N1} MB, at or over the 2 GB single-asset limit. " -f ($appArchiveBytes / 1MB)) +
          'A split installer would be needed; this packager deliberately does not do that silently.'
}

# --------------------------------------------------------------------------
# Stage the installer payload
# --------------------------------------------------------------------------

Write-Step 'Staging installer payload'

New-Item -ItemType Directory -Force -Path $stageDir | Out-Null
Copy-Item (Join-Path $InstSrcDir 'install.cmd') $stageDir

# Stamp the base name, so the parent-process walk in install.ps1 recognises
# the exe it was launched from. Regex matches any previously stamped value,
# which keeps this immune to a committed pre-stamped script.
(Get-Content (Join-Path $InstSrcDir 'install.ps1')) `
    -replace '^\$BaseName\s*=.*$', ('$BaseName    = "{0}"   # stamped by packager' -f $InstallBase) |
    Set-Content (Join-Path $stageDir 'install.ps1')

Copy-Item $SevenZr $stageDir
Copy-Item $appArchive $stageDir
Write-Ok 'install.cmd, install.ps1 (stamped), 7zr.exe, app archive'

& $SevenZr a -t7z $payload7z (Join-Path $stageDir '*') -mx=1 | Out-Host
if ($LASTEXITCODE -ne 0) { throw "7zr failed building the installer payload (exit $LASTEXITCODE)" }

# --------------------------------------------------------------------------
# Assemble the self-extractor: module + config + payload, byte-wise.
#
# NOT `cmd /c copy /b` - that passes the concat list as ONE quoted argument
# and cmd looks for a file literally named "a + b + c", with the error
# swallowed. ChitraMaya Batch 24g. PowerShell owns the bytes here and
# failures say why.
# --------------------------------------------------------------------------

Write-Step 'Assembling installer'

$sfxParts = @($SfxModule, (Join-Path $InstSrcDir 'sfx_config.txt'), $payload7z)
$outFs = [IO.File]::Create($installer)
try {
    foreach ($pf in $sfxParts) {
        $bytes = [IO.File]::ReadAllBytes((Resolve-Path $pf))
        $outFs.Write($bytes, 0, $bytes.Length)
        Write-Ok ("appended {0} ({1:N0} bytes)" -f (Split-Path -Leaf $pf), $bytes.Length)
    }
} finally {
    $outFs.Close()
}

if (-not (Test-Path $installer)) { throw 'Installer was not produced.' }

$installerBytes = (Get-Item $installer).Length
$sha = (Get-FileHash $installer -Algorithm SHA256).Hash

# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

Write-Step 'Done'
Write-Host ''
Write-Host "  $AppName $version" -ForegroundColor White
Write-Host ("  {0}" -f $installer)
Write-Host ("  {0:N1} MB" -f ($installerBytes / 1MB))
Write-Host ("  SHA256  {0}" -f $sha)
Write-Host ''
Write-Host '  ONE file. No split volumes, no "download all three".' -ForegroundColor Green
Write-Host ''
Write-Host '  Test on a machine that has never run this app:' -ForegroundColor Yellow
Write-Host '    1. run the installer; it should prompt for a folder and extract'
Write-Host "    2. launch $AppName.exe - no console window should appear"
Write-Host '    3. scan a folder; candidates should queue themselves'
Write-Host '    4. clean one file and confirm the -cleaned.mp4 plays'
Write-Host "    5. run $AppName-cli.exe scan <folder> in PowerShell"
Write-Host ''
