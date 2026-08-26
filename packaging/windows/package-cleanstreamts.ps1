# packaging/windows/cleanstreamts-packager.ps1
<#
.SYNOPSIS
    Build CleanStreamTS into a single self-extracting installer exe.

.DESCRIPTION
    Runs PyInstaller against cleanstreamts.spec, then wraps dist\CleanStreamTS
    into ONE self-extracting archive: dist\CleanStreamTS-install.exe

    Unlike ChitraMaya - whose 1.5 GB bundle has to be split across three files
    to fit GitHub's 2 GB asset limit - this app lands around 150 MB, so a
    single installer is possible and the whole "you need ALL THREE files"
    class of support problem never arises. The script refuses to fall back to
    a split silently: if the payload ever grows past the limit it stops and
    says so.

    Vendor files required in packaging\windows\vendor\ :
      7zSD.sfx    from the LZMA SDK  (7-zip.org/sdk.html -> bin\7zSD.sfx)
      7zr.exe     from the LZMA SDK  (7-zip.org/sdk.html -> bin\7zr.exe)
      ffmpeg\     LGPL ffmpeg.exe + ffprobe.exe (+ DLLs for a shared build)

    Modern 7-Zip "extra" packages no longer carry SFX modules; both vendor
    binaries come from the LZMA SDK bin\ folder.

.EXAMPLE
    powershell -ExecutionPolicy Bypass .\packaging\windows\cleanstreamts-packager.ps1
#>

[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [switch]$KeepDist
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$AppName    = 'CleanStreamTS'
$SpecDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot   = Resolve-Path (Join-Path $SpecDir '..\..')
$VendorDir  = Join-Path $SpecDir 'vendor'
$InstallDir = Join-Path $SpecDir 'installer'
$DistDir    = Join-Path $RepoRoot 'dist'
$PayloadDir = Join-Path $DistDir $AppName

$SfxModule  = Join-Path $VendorDir '7zSD.sfx'
$SevenZip   = Join-Path $VendorDir '7zr.exe'
$SfxConfig  = Join-Path $InstallDir 'sfx_config.txt'

$GithubAssetLimitBytes = 2GB

function Write-Step { param($m) Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Write-Ok   { param($m) Write-Host "  $m" -ForegroundColor Green }
function Write-Warn2{ param($m) Write-Host "  $m" -ForegroundColor Yellow }

# --------------------------------------------------------------------------
# Preflight - check EVERY input before spending minutes on a build
# --------------------------------------------------------------------------

Write-Step 'Preflight'

$version = & python -c "import sys; sys.path.insert(0, r'$RepoRoot'); import cleanstreamts; print(cleanstreamts.__version__)"
if (-not $version) { throw 'Could not read cleanstreamts.__version__' }
$version = $version.Trim()
Write-Ok "version $version"

$missing = @()
foreach ($f in @($SfxModule, $SevenZip, $SfxConfig)) {
    if (-not (Test-Path $f)) { $missing += $f }
}
if ($missing.Count -gt 0) {
    Write-Host ''
    Write-Host 'Missing required packaging inputs:' -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    Write-Host ''
    Write-Host 'See packaging\windows\vendor\README.md for where to get them.' -ForegroundColor Yellow
    throw 'Preflight failed.'
}
Write-Ok 'sfx module, 7zr, sfx config present'

$ffmpegDir = Join-Path $VendorDir 'ffmpeg'
if (Test-Path (Join-Path $ffmpegDir 'ffmpeg.exe')) {
    Write-Ok 'bundled ffmpeg present'
} else {
    Write-Warn2 'no vendor\ffmpeg\ffmpeg.exe - the app will need ffmpeg on PATH'
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
    Write-Warn2 'skipping build (-SkipBuild)'
}

if (-not (Test-Path $PayloadDir)) { throw "Payload folder not found: $PayloadDir" }

foreach ($exe in @("$AppName.exe", "$AppName-cli.exe")) {
    $p = Join-Path $PayloadDir $exe
    if (-not (Test-Path $p)) { throw "Expected executable missing from the build: $exe" }
    Write-Ok "found $exe"
}

$payloadBytes = (Get-ChildItem $PayloadDir -Recurse -File | Measure-Object Length -Sum).Sum
Write-Ok ("payload {0:N1} MB" -f ($payloadBytes / 1MB))

# --------------------------------------------------------------------------
# Clean stale archives - loudly. A silent -ErrorAction SilentlyContinue here
# masks a locked file (AV scan) and 7-Zip then fails with a misleading
# "multivolume" error. ChitraMaya Batch 24c.
# --------------------------------------------------------------------------

Write-Step 'Clearing stale archives'

$archive   = Join-Path $DistDir "$AppName-install.7z"
$installer = Join-Path $DistDir "$AppName-install.exe"

foreach ($stale in @($archive, $installer)) {
    if (-not (Test-Path $stale)) { continue }
    $removed = $false
    for ($try = 1; $try -le 5; $try++) {
        try {
            Remove-Item $stale -Force
            $removed = $true
            Write-Ok "removed stale $(Split-Path -Leaf $stale)"
            break
        } catch {
            Write-Warn2 "locked, retry $try/5: $(Split-Path -Leaf $stale)"
            Start-Sleep -Seconds 2
        }
    }
    if (-not $removed) {
        throw "Could not remove $stale - it is locked. Close anything using it (antivirus, Explorer preview) and re-run."
    }
}

# --------------------------------------------------------------------------
# Archive
# --------------------------------------------------------------------------

Write-Step 'Compressing'

Push-Location $DistDir
try {
    & $SevenZip a -t7z $archive $AppName -mx=7 -mmt=on | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "7zr failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

if (-not (Test-Path $archive)) { throw 'Archive was not produced.' }

$archiveBytes = (Get-Item $archive).Length
Write-Ok ("archive {0:N1} MB" -f ($archiveBytes / 1MB))

if ($archiveBytes -ge $GithubAssetLimitBytes) {
    throw ("Archive is {0:N1} MB, at or over the {1:N0} GB single-asset limit. " -f ($archiveBytes / 1MB), ($GithubAssetLimitBytes / 1GB)) +
          'A split installer would be needed - this packager deliberately does not do that silently.'
}

# --------------------------------------------------------------------------
# Assemble the self-extractor: sfx module + config + archive, byte-wise.
#
# NOT `cmd /c copy /b` - that passes the concat list as ONE quoted argument
# and cmd then looks for a file literally named "a + b + c", with the error
# swallowed. ChitraMaya Batch 24g. Direct byte concatenation, with real
# errors.
# --------------------------------------------------------------------------

Write-Step 'Assembling installer'

$parts = @($SfxModule, $SfxConfig, $archive)
$out = [System.IO.File]::Create($installer)
try {
    foreach ($part in $parts) {
        $bytes = [System.IO.File]::ReadAllBytes($part)
        $out.Write($bytes, 0, $bytes.Length)
        Write-Ok ("appended {0} ({1:N0} bytes)" -f (Split-Path -Leaf $part), $bytes.Length)
    }
} finally {
    $out.Close()
}

if (-not (Test-Path $installer)) { throw 'Installer was not produced.' }

$installerBytes = (Get-Item $installer).Length
$sha = (Get-FileHash $installer -Algorithm SHA256).Hash

Remove-Item $archive -Force
if (-not $KeepDist) {
    Write-Ok 'keeping dist payload folder (use -KeepDist:$false to remove)'
}

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
Write-Host '  Single-file installer - no split volumes, no "download all three".' -ForegroundColor Green
Write-Host ''
Write-Host '  Before publishing, test on a machine that has never run this app:' -ForegroundColor Yellow
Write-Host '    1. run the installer, confirm it extracts'
Write-Host "    2. launch $AppName.exe - no console window should appear"
Write-Host '    3. scan a folder, confirm candidates auto-queue'
Write-Host '    4. clean one file, confirm playback of the -cleaned.mp4'
Write-Host "    5. run $AppName-cli.exe scan <folder> in PowerShell - output should appear"
Write-Host ''
