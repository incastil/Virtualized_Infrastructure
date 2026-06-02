#Requires -Version 5.1
<#
.SYNOPSIS
    Automates file and directory backups with compression, rotation, and logging.

.DESCRIPTION
    Copies source directories to a backup destination using robocopy or
    Compress-Archive. Supports retention policies, incremental backup logic,
    timestamped archives, and a backup log for audit purposes.

.PARAMETER SourcePaths
    Array of source directories to back up.

.PARAMETER BackupRoot
    Root destination directory for backups.

.PARAMETER RetentionDays
    Number of days to retain old backups. Older backups are deleted.

.PARAMETER CompressBackup
    Switch to compress backup into a ZIP archive instead of folder copy.

.PARAMETER LogPath
    Path to backup log file. Defaults to BackupRoot\backup.log.

.EXAMPLE
    .\BackupAutomation.ps1 -SourcePaths "C:\Data","C:\Configs" -BackupRoot "D:\Backups"
    .\BackupAutomation.ps1 -SourcePaths "C:\Data" -BackupRoot "D:\Backups" -CompressBackup -RetentionDays 14
#>

param(
    [Parameter(Mandatory)]
    [string[]]$SourcePaths,

    [Parameter(Mandatory)]
    [string]$BackupRoot,

    [int]$RetentionDays = 30,

    [switch]$CompressBackup,

    [string]$LogPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

if (-not $LogPath) {
    $LogPath = Join-Path $BackupRoot "backup.log"
}

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $entry = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [$Level] $Message"
    Add-Content -Path $LogPath -Value $entry -ErrorAction SilentlyContinue
    $color = switch ($Level) {
        "ERROR" { "Red" }
        "WARN"  { "Yellow" }
        "OK"    { "Green" }
        default { "White" }
    }
    Write-Host $entry -ForegroundColor $color
}

function Invoke-BackupSource {
    param([string]$Source)

    if (-not (Test-Path $Source)) {
        Write-Log "Source not found: $Source" -Level "ERROR"
        return $false
    }

    $sourceName = Split-Path $Source -Leaf
    $backupName = "${sourceName}_${timestamp}"

    if ($CompressBackup) {
        $archivePath = Join-Path $BackupRoot "${backupName}.zip"
        Write-Log "Compressing '$Source' -> '$archivePath'"
        try {
            Compress-Archive -Path $Source -DestinationPath $archivePath -Force
            $sizeKB = [math]::Round((Get-Item $archivePath).Length / 1KB, 1)
            Write-Log "Compressed OK. Size: ${sizeKB} KB" -Level "OK"
            return $true
        } catch {
            Write-Log "Compress failed: $_" -Level "ERROR"
            return $false
        }
    } else {
        $destPath = Join-Path $BackupRoot $backupName
        Write-Log "Copying '$Source' -> '$destPath' (robocopy)"
        try {
            $robocopyArgs = @($Source, $destPath, "/E", "/R:2", "/W:5", "/NP", "/LOG+:$LogPath")
            $proc = Start-Process -FilePath "robocopy" -ArgumentList $robocopyArgs `
                        -Wait -PassThru -WindowStyle Hidden
            # Robocopy exit codes 0-7 = success
            if ($proc.ExitCode -le 7) {
                Write-Log "Robocopy OK (exit $($proc.ExitCode))" -Level "OK"
                return $true
            } else {
                Write-Log "Robocopy error (exit $($proc.ExitCode))" -Level "ERROR"
                return $false
            }
        } catch {
            Write-Log "Robocopy failed: $_" -Level "ERROR"
            return $false
        }
    }
}

function Remove-OldBackups {
    param([string]$Root, [int]$Days)

    $cutoff = (Get-Date).AddDays(-$Days)
    Write-Log "Applying retention policy: remove items older than $Days days (before $($cutoff.ToString('yyyy-MM-dd')))"

    $old = Get-ChildItem -Path $Root -ErrorAction SilentlyContinue |
           Where-Object { $_.LastWriteTime -lt $cutoff -and $_.Name -ne "backup.log" }

    if ($old.Count -eq 0) {
        Write-Log "No old backups to remove."
        return
    }

    foreach ($item in $old) {
        try {
            Remove-Item -Path $item.FullName -Recurse -Force
            Write-Log "Removed: $($item.Name)" -Level "WARN"
        } catch {
            Write-Log "Failed to remove $($item.Name): $_" -Level "ERROR"
        }
    }
    Write-Log "Retention cleanup complete. $($old.Count) item(s) removed."
}

function Get-BackupSummary {
    param([string]$Root)

    $items = Get-ChildItem -Path $Root -ErrorAction SilentlyContinue |
             Where-Object { $_.Name -ne "backup.log" }

    $totalSizeMB = ($items | Measure-Object -Property Length -Sum -ErrorAction SilentlyContinue).Sum
    $totalSizeMB = if ($totalSizeMB) { [math]::Round($totalSizeMB / 1MB, 2) } else { 0 }

    Write-Log "Backup root '$Root' contains $($items.Count) item(s), approx ${totalSizeMB} MB total."
}

# Main
Write-Log "===== Backup started ====="
Write-Log "Timestamp: $timestamp | Sources: $($SourcePaths -join ', ') | Compress: $CompressBackup"

if (-not (Test-Path $BackupRoot)) {
    Write-Log "Creating backup root: $BackupRoot"
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
}

$success = 0
$fail    = 0

foreach ($src in $SourcePaths) {
    $ok = Invoke-BackupSource -Source $src
    if ($ok) { $success++ } else { $fail++ }
}

Remove-OldBackups -Root $BackupRoot -Days $RetentionDays
Get-BackupSummary -Root $BackupRoot

Write-Log "===== Backup complete: $success succeeded, $fail failed ====="
