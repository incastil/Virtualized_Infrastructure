#Requires -Version 5.1
<#
.SYNOPSIS
    Collects comprehensive system inventory information from local or remote hosts.

.DESCRIPTION
    Gathers hardware specs, OS info, installed software, network configuration,
    running services, and disk usage. Outputs to console and optional CSV report.

.PARAMETER ComputerName
    Target computer(s). Defaults to local machine.

.PARAMETER OutputPath
    Directory to save CSV report. Defaults to script directory.

.PARAMETER ExportCSV
    Switch to enable CSV export.

.EXAMPLE
    .\Collect-SystemInfo.ps1
    .\Collect-SystemInfo.ps1 -ComputerName "SERVER01" -ExportCSV
    .\Collect-SystemInfo.ps1 -ComputerName "SERVER01","SERVER02" -ExportCSV -OutputPath "C:\Reports"
#>

param(
    [string[]]$ComputerName = $env:COMPUTERNAME,
    [string]$OutputPath = $PSScriptRoot,
    [switch]$ExportCSV
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-SystemInventory {
    param([string]$Computer)

    Write-Host "`n[$Computer] Collecting system information..." -ForegroundColor Cyan

    $result = [PSCustomObject]@{
        Timestamp       = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        ComputerName    = $Computer
        OSName          = "N/A"
        OSVersion       = "N/A"
        OSBuildNumber   = "N/A"
        Architecture    = "N/A"
        LastBoot        = "N/A"
        UptimeDays      = "N/A"
        CPU             = "N/A"
        CPUCores        = "N/A"
        CPULogicalCores = "N/A"
        TotalRAM_GB     = "N/A"
        FreeRAM_GB      = "N/A"
        RAMUsagePct     = "N/A"
        Hostname        = "N/A"
        Domain          = "N/A"
        IPAddresses     = "N/A"
        DiskInfo        = "N/A"
        InstalledSW     = "N/A"
    }

    try {
        $scriptBlock = {
            $os        = Get-CimInstance -ClassName Win32_OperatingSystem
            $cpu       = Get-CimInstance -ClassName Win32_Processor | Select-Object -First 1
            $cs        = Get-CimInstance -ClassName Win32_ComputerSystem
            $network   = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                         Where-Object { $_.InterfaceAlias -notlike "*Loopback*" } |
                         Select-Object -ExpandProperty IPAddress
            $disks     = Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Used -ne $null }
            $software  = Get-ItemProperty "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*" `
                            -ErrorAction SilentlyContinue |
                         Where-Object { $_.DisplayName } |
                         Select-Object DisplayName, DisplayVersion |
                         Sort-Object DisplayName

            $uptime    = (Get-Date) - $os.LastBootUpTime
            $totalRAM  = [math]::Round($cs.TotalPhysicalMemory / 1GB, 2)
            $freeRAM   = [math]::Round($os.FreePhysicalMemory / 1MB, 2)
            $usedRAM   = $totalRAM - $freeRAM
            $ramPct    = [math]::Round(($usedRAM / $totalRAM) * 100, 1)

            $diskSummary = $disks | ForEach-Object {
                $usedGB  = [math]::Round($_.Used / 1GB, 2)
                $freeGB  = [math]::Round($_.Free / 1GB, 2)
                $totalGB = [math]::Round(($_.Used + $_.Free) / 1GB, 2)
                "$($_.Name): ${usedGB}GB used / ${totalGB}GB total (${freeGB}GB free)"
            }

            [PSCustomObject]@{
                OSName          = $os.Caption
                OSVersion       = $os.Version
                OSBuildNumber   = $os.BuildNumber
                Architecture    = $os.OSArchitecture
                LastBoot        = $os.LastBootUpTime.ToString("yyyy-MM-dd HH:mm:ss")
                UptimeDays      = [math]::Round($uptime.TotalDays, 2)
                CPU             = $cpu.Name.Trim()
                CPUCores        = $cpu.NumberOfCores
                CPULogicalCores = $cpu.NumberOfLogicalProcessors
                TotalRAM_GB     = $totalRAM
                FreeRAM_GB      = $freeRAM
                RAMUsagePct     = $ramPct
                Hostname        = $env:COMPUTERNAME
                Domain          = $cs.Domain
                IPAddresses     = ($network -join ", ")
                DiskInfo        = ($diskSummary -join " | ")
                InstalledSW     = ($software | ForEach-Object { "$($_.DisplayName) $($_.DisplayVersion)" } | Select-Object -First 20) -join "; "
            }
        }

        if ($Computer -eq $env:COMPUTERNAME) {
            $data = & $scriptBlock
        } else {
            $data = Invoke-Command -ComputerName $Computer -ScriptBlock $scriptBlock -ErrorAction Stop
        }

        $result.OSName          = $data.OSName
        $result.OSVersion       = $data.OSVersion
        $result.OSBuildNumber   = $data.OSBuildNumber
        $result.Architecture    = $data.Architecture
        $result.LastBoot        = $data.LastBoot
        $result.UptimeDays      = $data.UptimeDays
        $result.CPU             = $data.CPU
        $result.CPUCores        = $data.CPUCores
        $result.CPULogicalCores = $data.CPULogicalCores
        $result.TotalRAM_GB     = $data.TotalRAM_GB
        $result.FreeRAM_GB      = $data.FreeRAM_GB
        $result.RAMUsagePct     = $data.RAMUsagePct
        $result.Hostname        = $data.Hostname
        $result.Domain          = $data.Domain
        $result.IPAddresses     = $data.IPAddresses
        $result.DiskInfo        = $data.DiskInfo
        $result.InstalledSW     = $data.InstalledSW

        # Print to console
        Write-Host "  OS:         $($result.OSName) (Build $($result.OSBuildNumber))" -ForegroundColor Green
        Write-Host "  CPU:        $($result.CPU) | Cores: $($result.CPUCores) | Logical: $($result.CPULogicalCores)"
        Write-Host "  RAM:        $($result.TotalRAM_GB) GB total | $($result.FreeRAM_GB) GB free | $($result.RAMUsagePct)% used"
        Write-Host "  Uptime:     $($result.UptimeDays) days (since $($result.LastBoot))"
        Write-Host "  IP:         $($result.IPAddresses)"
        Write-Host "  Domain:     $($result.Domain)"
        Write-Host "  Disk:       $($result.DiskInfo)"

    } catch {
        Write-Warning "[$Computer] Failed to collect info: $_"
        $result.OSName = "ERROR: $_"
    }

    return $result
}

# Main
$allResults = @()

foreach ($computer in $ComputerName) {
    $allResults += Get-SystemInventory -Computer $computer
}

if ($ExportCSV) {
    $timestamp  = Get-Date -Format "yyyyMMdd_HHmmss"
    $reportFile = Join-Path $OutputPath "SystemInventory_$timestamp.csv"
    $allResults | Export-Csv -Path $reportFile -NoTypeInformation -Encoding UTF8
    Write-Host "`nReport saved: $reportFile" -ForegroundColor Yellow
}

Write-Host "`nInventory complete. $($allResults.Count) host(s) scanned." -ForegroundColor Cyan
$allResults
