#Requires -Version 5.1
<#
.SYNOPSIS
    Monitors Windows services and generates status reports with alerting.

.DESCRIPTION
    Checks status of critical services on local or remote hosts.
    Flags stopped services that should be running. Optionally attempts
    auto-restart and exports HTML/CSV reports.

.PARAMETER ComputerName
    Target computer(s). Defaults to local machine.

.PARAMETER CriticalServices
    List of service names to treat as critical. Stopped critical services trigger alerts.

.PARAMETER OutputPath
    Directory to save reports.

.PARAMETER ExportReport
    Switch to enable report export (CSV + HTML).

.PARAMETER AutoRestart
    Switch to attempt automatic restart of stopped critical services.

.EXAMPLE
    .\ServiceStatusReport.ps1
    .\ServiceStatusReport.ps1 -ComputerName "SERVER01" -ExportReport
    .\ServiceStatusReport.ps1 -AutoRestart -ExportReport -OutputPath "C:\Reports"
#>

param(
    [string[]]$ComputerName = $env:COMPUTERNAME,
    [string[]]$CriticalServices = @(
        "Spooler", "wuauserv", "WinRM", "EventLog",
        "LanmanServer", "LanmanWorkstation", "W32Time",
        "Dnscache", "BITS", "Schedule", "SamSs"
    ),
    [string]$OutputPath = $PSScriptRoot,
    [switch]$ExportReport,
    [switch]$AutoRestart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "SilentlyContinue"

function Get-ServiceReport {
    param(
        [string]$Computer,
        [string[]]$Critical
    )

    Write-Host "`n[$Computer] Checking services..." -ForegroundColor Cyan

    $services = @()

    try {
        $scriptBlock = {
            param($Critical)
            $svcs = Get-Service -ErrorAction SilentlyContinue | Select-Object Name, DisplayName, Status, StartType
            foreach ($svc in $svcs) {
                [PSCustomObject]@{
                    Name        = $svc.Name
                    DisplayName = $svc.DisplayName
                    Status      = $svc.Status.ToString()
                    StartType   = $svc.StartType.ToString()
                    IsCritical  = $Critical -contains $svc.Name
                    IsAlert     = ($Critical -contains $svc.Name) -and ($svc.Status -ne "Running")
                }
            }
        }

        if ($Computer -eq $env:COMPUTERNAME) {
            $services = & $scriptBlock -Critical $Critical
        } else {
            $services = Invoke-Command -ComputerName $Computer -ScriptBlock $scriptBlock `
                            -ArgumentList (,$Critical) -ErrorAction Stop
        }
    } catch {
        Write-Warning "[$Computer] Cannot connect: $_"
        return @()
    }

    # Auto-restart stopped critical services
    if ($AutoRestart) {
        $stopped = $services | Where-Object { $_.IsAlert }
        foreach ($svc in $stopped) {
            Write-Host "  [RESTART] Attempting to restart: $($svc.DisplayName)" -ForegroundColor Yellow
            try {
                if ($Computer -eq $env:COMPUTERNAME) {
                    Start-Service -Name $svc.Name -ErrorAction Stop
                    $svc.Status = "Running (Restarted)"
                    $svc.IsAlert = $false
                } else {
                    Invoke-Command -ComputerName $Computer -ScriptBlock {
                        param($n) Start-Service -Name $n -ErrorAction Stop
                    } -ArgumentList $svc.Name
                    $svc.Status = "Running (Restarted)"
                    $svc.IsAlert = $false
                }
                Write-Host "  [OK] $($svc.DisplayName) restarted." -ForegroundColor Green
            } catch {
                Write-Warning "  [FAIL] Could not restart $($svc.DisplayName): $_"
            }
        }
    }

    # Console summary
    $running = ($services | Where-Object { $_.Status -eq "Running" }).Count
    $stopped = ($services | Where-Object { $_.Status -ne "Running" }).Count
    $alerts  = ($services | Where-Object { $_.IsAlert }).Count

    Write-Host "  Total:   $($services.Count) services"
    Write-Host "  Running: $running" -ForegroundColor Green
    Write-Host "  Stopped: $stopped" -ForegroundColor $(if ($stopped -gt 0) { "Yellow" } else { "Green" })

    if ($alerts -gt 0) {
        Write-Host "  ALERTS:  $alerts critical service(s) stopped!" -ForegroundColor Red
        $services | Where-Object { $_.IsAlert } | ForEach-Object {
            Write-Host "    - $($_.DisplayName) ($($_.Name))" -ForegroundColor Red
        }
    } else {
        Write-Host "  All critical services running." -ForegroundColor Green
    }

    return $services | Select-Object `
        @{N="Computer";E={$Computer}},
        Name, DisplayName, Status, StartType, IsCritical, IsAlert
}

function New-HTMLReport {
    param($Data, [string]$FilePath)

    $rows = $Data | ForEach-Object {
        $rowColor = if ($_.IsAlert) { "#ffcccc" } elseif ($_.IsCritical) { "#fff3cd" } else { "white" }
        $status   = if ($_.IsAlert) { "<strong style='color:red'>$($_.Status)</strong>" } else { $_.Status }
        "<tr style='background:$rowColor'><td>$($_.Computer)</td><td>$($_.Name)</td><td>$($_.DisplayName)</td><td>$status</td><td>$($_.StartType)</td><td>$(if($_.IsCritical){'Yes'}else{''})</td></tr>"
    }

    $html = @"
<!DOCTYPE html>
<html>
<head>
<title>Service Status Report - $(Get-Date -Format 'yyyy-MM-dd HH:mm')</title>
<style>
  body { font-family: Arial, sans-serif; margin: 20px; }
  table { border-collapse: collapse; width: 100%; }
  th { background: #2c3e50; color: white; padding: 8px; text-align: left; }
  td { padding: 6px 8px; border-bottom: 1px solid #ddd; }
  tr:hover { filter: brightness(95%); }
  h2 { color: #2c3e50; }
</style>
</head>
<body>
<h2>Service Status Report</h2>
<p>Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | Hosts: $($Data.Computer | Sort-Object -Unique | Join-String -Separator ', ')</p>
<table>
<thead><tr><th>Computer</th><th>Service Name</th><th>Display Name</th><th>Status</th><th>Start Type</th><th>Critical</th></tr></thead>
<tbody>$($rows -join "`n")</tbody>
</table>
</body>
</html>
"@
    $html | Out-File -FilePath $FilePath -Encoding UTF8
}

# Main
$allServices = @()

foreach ($computer in $ComputerName) {
    $allServices += Get-ServiceReport -Computer $computer -Critical $CriticalServices
}

if ($ExportReport -and $allServices.Count -gt 0) {
    $ts      = Get-Date -Format "yyyyMMdd_HHmmss"
    $csvFile  = Join-Path $OutputPath "ServiceReport_$ts.csv"
    $htmlFile = Join-Path $OutputPath "ServiceReport_$ts.html"

    $allServices | Export-Csv -Path $csvFile -NoTypeInformation -Encoding UTF8
    New-HTMLReport -Data $allServices -FilePath $htmlFile

    Write-Host "`nReports saved:" -ForegroundColor Yellow
    Write-Host "  CSV:  $csvFile"
    Write-Host "  HTML: $htmlFile"
}

$alertCount = ($allServices | Where-Object { $_.IsAlert }).Count
Write-Host "`nScan complete. $alertCount alert(s) detected." -ForegroundColor $(if ($alertCount -gt 0) { "Red" } else { "Cyan" })
