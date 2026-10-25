# Windows setup for the Z8 Wi-Fi FTP drop box.
# Run from an admin PowerShell in this folder:
#   powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
#   powershell -ExecutionPolicy Bypass -File .\install-windows.ps1 -Uninstall
# -Yes answers every question with yes (used by the automated test).
#Requires -RunAsAdministrator
param([switch]$Uninstall, [switch]$Yes)
$ErrorActionPreference = "Stop"

$Here = $PSScriptRoot
$Task = "Z8 Wi-Fi Drop"
$Rules = @(
    @{ Name = "Z8 Wi-Fi FTP";   Port = "2121" },
    @{ Name = "Z8 FTP passive"; Port = "60000-60100" }  # must match common.py
)
$Incoming = "C:\QDAM\Listings\Incoming"
$State = Join-Path $env:USERPROFILE "AppData\Roaming\z8-wifi-drop"  # must match common.py
$Login = Join-Path $State "login.json"
$Desktop = [Environment]::GetFolderPath("Desktop")
$Shortcut = Join-Path $Desktop "Z8 Photo Sorter.lnk"
$Notes = Join-Path $Desktop "Z8 camera settings.txt"

function Ask($question) {
    if ($Yes) { return $true }
    return (Read-Host "$question (y/n)") -eq "y"
}

function Remove-DropBox {
    Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $Task -Confirm:$false -ErrorAction SilentlyContinue
    foreach ($r in $Rules) {
        Remove-NetFirewallRule -DisplayName $r.Name -ErrorAction SilentlyContinue
    }
}

if ($Uninstall) {
    Remove-DropBox
    Remove-Item $Shortcut -ErrorAction SilentlyContinue
    Write-Host "Removed the startup task, firewall rules and shortcut. Photos in $Incoming are kept."
    exit 0
}

# 1. Python
$pyExe = $null
$pyArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) { $pyExe = "py"; $pyArgs = @("-3") }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $pyExe = "python" }
if (-not $pyExe) {
    if (Ask "Python is missing. Install it now with winget?") {
        winget install -e --id Python.Python.3.13 --accept-source-agreements --accept-package-agreements
        Write-Host "Python installed. Close this window, open a new admin PowerShell, and run this script again."
        exit 0
    }
    throw "Python is missing. Install Python 3 from python.org, then run this again."
}

# 2. Private venv with pyftpdlib
Write-Host "Setting up Python packages..."
& $pyExe @pyArgs -m venv "$Here\.venv"
if (-not (Test-Path "$Here\.venv\Scripts\python.exe")) {
    # The Microsoft Store "python" stub exists but cannot make a venv.
    throw "Python could not make a venv. Install Python 3 from python.org (tick 'Add to PATH'), then run this again."
}
& "$Here\.venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check pyftpdlib
if ($LASTEXITCODE -ne 0) { throw "pip could not install pyftpdlib. Check the internet connection." }

# 3. Destination folder
New-Item -ItemType Directory -Force $Incoming | Out-Null

# 4. Network profile. The firewall rules below only apply on Private networks.
$public = Get-NetConnectionProfile | Where-Object NetworkCategory -eq "Public"
foreach ($p in $public) {
    if (Ask "Network '$($p.Name)' is Public. Set it to Private (needed so the camera can reach this PC)?") {
        Set-NetConnectionProfile -InterfaceIndex $p.InterfaceIndex -NetworkCategory Private
    } else {
        Write-Warning "'$($p.Name)' stays Public. The camera cannot reach this PC on it."
    }
}

# 5. Firewall: home subnet only, Private profile only
Remove-DropBox
foreach ($r in $Rules) {
    New-NetFirewallRule -DisplayName $r.Name -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $r.Port -RemoteAddress LocalSubnet -Profile Private | Out-Null
}

# 6. Run at startup, hidden, even before anyone logs in (S4U: no password stored).
#    pythonw has no console; server.py logs to server.log in the state folder.
$action = New-ScheduledTaskAction -Execute "$Here\.venv\Scripts\pythonw.exe" `
    -Argument "`"$Here\server.py`"" -WorkingDirectory $Here
$triggers = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME))
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $Task -Action $action -Trigger $triggers -Principal $principal `
    -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $Task

# 7. Desktop shortcut for the photo sorter (watcher.py)
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($Shortcut)
$lnk.TargetPath = "$Here\.venv\Scripts\python.exe"
$lnk.Arguments = "`"$Here\watcher.py`""
$lnk.WorkingDirectory = $Here
$lnk.Description = "Rename new Z8 photos to WATCHID_01.jpg ..."
$lnk.Save()

# 8. Wait for the server, then print and save what to type on the camera
for ($i = 0; $i -lt 40 -and -not (Test-Path $Login); $i++) { Start-Sleep -Milliseconds 500 }
if (-not (Test-Path $Login)) {
    throw "The server did not start. See $State\server.log, or run: $Here\.venv\Scripts\python.exe $Here\server.py"
}
$creds = Get-Content $Login -Raw | ConvertFrom-Json
$ips = @(Get-NetIPConfiguration | Where-Object IPv4DefaultGateway | ForEach-Object { $_.IPv4Address.IPAddress })

$text = @"
Z8 camera settings for this PC ($env:COMPUTERNAME)
Connect to FTP server > Network settings > Create profile > Configure manually > Wireless LAN

  FTP > Server type:     FTP
  FTP > Address:         $($ips -join '   or   ')
  FTP > Folder:          (leave blank)
  FTP > Port:            2121
  FTP > PASV mode:       ON
  FTP > Anonymous login: OFF
  FTP > User ID:         $($creds.user)
  FTP > Password:        $($creds.password)

Photos land in: $Incoming
Photo sorter:   double-click "Z8 Photo Sorter" on the desktop
"@
Set-Content -Path $Notes -Value $text
Write-Host ""
Write-Host $text
Write-Host ""
Write-Host "Saved to the desktop as 'Z8 camera settings.txt'."
if ($ips.Count -gt 1) {
    Write-Warning "This PC has more than one network address. Use the one on the same network as the camera."
}
