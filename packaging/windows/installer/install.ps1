# packaging/windows/installer/install.ps1
# CleanStreamTS installer brain - runs from inside the CleanStreamTS-install.exe SFX.
#
# The .exe users double-click is a self-extractor that unpacks this script
# (plus 7zr.exe and the app archive) to a temp folder and runs it. This
# script then:
#   1. finds the folder the .exe was run from (parent-process walk, with
#      sensible fallbacks) to pick a sensible default install location,
#   2. asks where to install, defaulting to a LOCAL FIXED disk,
#   3. extracts with the bundled 7zr.exe and prints what to do next.
#
# Adapted from ChitraMaya's installer. The difference: ChitraMaya's payload
# is ~1.5 GB and has to ship as separate .7z volumes beside the exe, so its
# installer verifies every volume is present and names any that are missing.
# CleanStreamTS is small enough to live INSIDE the exe, so there are no
# volumes to find and no "you need all three files" class of support problem.
# Everything else - the destination logic and its field lessons - is kept.
#
# ASCII-only output. Always ends with a Read-Host so the window never
# vanishes before the user reads the message.

$ErrorActionPreference = "SilentlyContinue"

$AppName     = "CleanStreamTS"
$BaseName    = "CleanStreamTS-install"   # stamped by the packager
$ArchiveName = "CleanStreamTS-app.7z"    # sits beside this script in the SFX temp dir
$ReleasesUrl = "https://github.com/mountlord/clean-stream-ts/releases"

function Find-InstallerDir {
    # The SFX exe is our grandparent process (exe -> cmd -> powershell).
    try {
        $me = Get-CimInstance Win32_Process -Filter "ProcessId=$PID"
        $p  = $me
        for ($i = 0; $i -lt 4 -and $p; $i++) {
            $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ParentProcessId)"
            if ($p -and $p.ExecutablePath -and
                ([IO.Path]::GetFileName($p.ExecutablePath) -like "$BaseName*.exe")) {
                return (Split-Path $p.ExecutablePath -Parent)
            }
        }
    } catch { }
    return $null
}

Write-Host ""
Write-Host "=============================================================" -ForegroundColor Cyan
Write-Host "  CleanStreamTS Installer" -ForegroundColor Cyan
Write-Host "=============================================================" -ForegroundColor Cyan
Write-Host ""

# The app archive travels inside the exe, so a missing one means a corrupt
# or truncated download - not a user mistake. Say so plainly.
$archivePath = Join-Path $PSScriptRoot $ArchiveName
if (-not (Test-Path $archivePath)) {
    Write-Host "  *** THE APPLICATION ARCHIVE IS MISSING ***" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Expected inside this installer:" -ForegroundColor Yellow
    Write-Host "      $ArchiveName"
    Write-Host ""
    Write-Host "  This almost always means the download did not finish, or the" -ForegroundColor Yellow
    Write-Host "  .exe was modified. Download it again from:" -ForegroundColor Yellow
    Write-Host "      $ReleasesUrl"
    Write-Host ""
    Read-Host "  Press Enter to close"
    exit 1
}

$srcDir = Find-InstallerDir
if (-not $srcDir) { $srcDir = (Get-Location).Path }

# CM-101 (ChitraMaya field bug 2026-08-16): where the installer WAS RUN and
# where the app should INSTALL are two different questions. Running the exe
# from a terminal in C:\MyPrograms with the exe itself on a mapped drive used
# to install onto the mapped drive. The destination is explicit now: prefer
# the terminal's working directory when it is a real local folder, else the
# exe's folder if that is local, else the user profile. The prompt below
# always allows something else, so a wrong guess costs one keystroke.
function Test-LocalFixedPath([string]$p) {
    # True only for a path on a LOCAL FIXED disk. Mapped network drives
    # (DriveType 4) and UNC paths are excluded.
    try {
        if (-not $p) { return $false }
        if ($p.StartsWith("\\")) { return $false }          # UNC
        $root = [IO.Path]::GetPathRoot($p)
        if (-not $root) { return $false }
        $ld = Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='{0}'" -f $root.TrimEnd('\')) -ErrorAction Stop
        return ($ld -and [int]$ld.DriveType -eq 3)          # 3 = local fixed
    } catch { return $false }
}

$tmpRoot = [IO.Path]::GetTempPath().TrimEnd('\')
$cwd = $null
try {
    $c = (Get-Location).Path
    if ($c -and (Test-Path $c) -and
        -not $c.StartsWith($tmpRoot, [StringComparison]::OrdinalIgnoreCase) -and
        -not $c.StartsWith($PSScriptRoot, [StringComparison]::OrdinalIgnoreCase) -and
        (Test-LocalFixedPath $c)) {
        $cwd = $c
    }
} catch { }

if ($cwd) {
    $destDefault = $cwd
} elseif (Test-LocalFixedPath $srcDir) {
    $destDefault = $srcDir
} else {
    $destDefault = $env:USERPROFILE
    Write-Host "  NOTE: this installer is running from a network or mapped" -ForegroundColor Yellow
    Write-Host "  drive ($srcDir)." -ForegroundColor Yellow
    Write-Host "  Installing THERE would put the program on that share, so the" -ForegroundColor Yellow
    Write-Host "  default below is a local folder instead." -ForegroundColor Yellow
    Write-Host ""
}

Write-Host "  CleanStreamTS will be installed as a '$AppName' folder inside:" -ForegroundColor Cyan
Write-Host ("      {0}" -f $destDefault)
Write-Host ""
$destInput = Read-Host "  Press Enter to accept, or type a different folder"
$destDir = if ($destInput -and $destInput.Trim()) { $destInput.Trim().Trim('"') } else { $destDefault }
if (-not (Test-Path $destDir)) {
    New-Item -ItemType Directory -Force -Path $destDir | Out-Null
}
if (-not (Test-Path $destDir)) {
    Write-Host ""
    Write-Host "  *** CANNOT CREATE FOLDER: $destDir ***" -ForegroundColor Red
    Write-Host "  Check the path and permissions, then run the installer again." -ForegroundColor Yellow
    Write-Host ""
    Read-Host "  Press Enter to close"
    exit 1
}

Write-Host ""
Write-Host "  Extracting..." -ForegroundColor Cyan
Write-Host ""

$sevenZr = Join-Path $PSScriptRoot "7zr.exe"
& $sevenZr x -y ("-o{0}" -f $destDir) $archivePath
$rc = $LASTEXITCODE

Write-Host ""
if ($rc -eq 0) {
    $installedDir = Join-Path $destDir $AppName
    $installedCli = Join-Path $installedDir ("{0}-cli.exe" -f $AppName)

    # Verify the INSTALLED copy, not just that extraction returned 0.
    #
    # A clean-machine failure is what put this here: the bundle contained
    # everything, extraction succeeded, and the app still died with
    # TemplateNotFound the first time its window opened - because it looked
    # for its own resources in the wrong folder. Nothing before this point
    # would have noticed. So the installer asks the app itself, on the user's
    # machine, in its final location.
    Write-Host "  Verifying the installed copy..." -ForegroundColor Cyan
    Write-Host ""

    $checkRc = 1
    if (Test-Path $installedCli) {
        & $installedCli self-check
        $checkRc = $LASTEXITCODE
    } else {
        Write-Host ("  *** {0}-cli.exe is missing from the install folder ***" -f $AppName) -ForegroundColor Red
    }

    Write-Host ""
    if ($checkRc -eq 0) {
        Write-Host "  DONE. CleanStreamTS was installed to:" -ForegroundColor Green
        Write-Host ("      {0}" -f $installedDir)
        Write-Host ""
        Write-Host "  Next steps:" -ForegroundColor Cyan
        Write-Host ("    1. Open the folder and run {0}.exe" -f $AppName)
        Write-Host "    2. Point it at a folder of downloaded videos and scan."
        Write-Host ""
        Write-Host ("    {0}-cli.exe is the same tool for the command line." -f $AppName)
    } else {
        Write-Host "  *** THE INSTALLED COPY FAILED ITS SELF-CHECK ***" -ForegroundColor Red
        Write-Host ""
        Write-Host "  The files were extracted to:" -ForegroundColor Yellow
        Write-Host ("      {0}" -f $installedDir)
        Write-Host ""
        Write-Host "  but the application cannot find something it needs (see the" -ForegroundColor Yellow
        Write-Host "  FAIL lines above). This is a fault in the build, not" -ForegroundColor Yellow
        Write-Host "  something you did wrong." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  Please report it with the lines above:" -ForegroundColor Yellow
        Write-Host "      $ReleasesUrl"
        $rc = $checkRc
    }
} else {
    Write-Host "  *** EXTRACTION FAILED (7-Zip exit code $rc) ***" -ForegroundColor Red
    Write-Host ""
    Write-Host "  This usually means the installer download is INCOMPLETE or" -ForegroundColor Yellow
    Write-Host "  CORRUPT. Download it again from:" -ForegroundColor Yellow
    Write-Host "      $ReleasesUrl"
    Write-Host ""
    Write-Host "  If it keeps failing, check you have free space on the target" -ForegroundColor Yellow
    Write-Host "  drive and that antivirus is not blocking the extraction." -ForegroundColor Yellow
}
Write-Host ""
Read-Host "  Press Enter to close"
exit $rc
