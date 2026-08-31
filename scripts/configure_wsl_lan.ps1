param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d{1,3}(\.\d{1,3}){3}$')]
    [string]$WslAddress,

    [ValidateSet('Private', 'Domain', 'Private,Domain', 'Any')]
    [string]$FirewallProfile = 'Any'
)

$ErrorActionPreference = 'Stop'
$ruleName = "Continual Harness LAN TCP $Port"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-LanAddresses {
    return @(
        Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred -ErrorAction SilentlyContinue |
            Where-Object {
                $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and
                $_.InterfaceAlias -notmatch '^(vEthernet|Loopback|WSL|Default Switch)'
            } |
            Sort-Object InterfaceMetric, PrefixLength |
            Select-Object -ExpandProperty IPAddress -Unique
    )
}

function Get-PortProxyRows {
    $proxyTable = (& netsh interface portproxy show v4tov4 2>$null | Out-String)
    return @(
        foreach ($line in $proxyTable -split "`r?`n") {
            if ($line -match '^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+(\d+)\s+(\d{1,3}(?:\.\d{1,3}){3})\s+(\d+)\s*$') {
                [pscustomobject]@{
                    ListenAddress  = $Matches[1]
                    ListenPort     = [int]$Matches[2]
                    ConnectAddress = $Matches[3]
                    ConnectPort    = [int]$Matches[4]
                }
            }
        }
    )
}

function Test-PortProxyReady {
    $lanAddresses = @(Get-LanAddresses)
    if ($lanAddresses.Count -eq 0) {
        return $false
    }

    $proxyRows = @(Get-PortProxyRows)
    $rulesForPort = $proxyRows | Where-Object {
        $_.ListenPort -eq $Port -and $_.ConnectPort -eq $Port
    }

    if ($lanAddresses -contains $WslAddress) {
        # Mirrored WSL networking already shares the Windows LAN address.
        # Any portproxy here would forward back into itself.
        if ($rulesForPort) {
            return $false
        }
    } else {
        $wildcardRule = $rulesForPort | Where-Object {
            $_.ListenAddress -eq '0.0.0.0'
        }
        if ($wildcardRule) {
            return $false
        }

        foreach ($address in $lanAddresses) {
            $matchingRule = $rulesForPort | Where-Object {
                $_.ListenAddress -eq $address -and
                $_.ConnectAddress -eq $WslAddress
            }
            if (-not $matchingRule) {
                return $false
            }
        }
    }

    $firewallRule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue |
        Where-Object { $_.Enabled -eq 'True' } |
        Select-Object -First 1
    if ($null -eq $firewallRule) {
        return $false
    }
    if ($FirewallProfile -eq 'Any' -and "$($firewallRule.Profile)" -ne 'Any') {
        return $false
    }
    return $true
}

function Write-LanUrls {
    $addresses = @(Get-LanAddresses)

    if ($addresses) {
        Write-Output "LAN access configured:"
        foreach ($address in $addresses) {
            Write-Output "  Live:     http://${address}:$Port/stream"
            Write-Output "  Timeline: http://${address}:$Port/timeline"
        }
    } else {
        Write-Output "LAN forwarding is configured on TCP $Port. Could not determine the Windows LAN address."
    }
}

if (-not (Test-IsAdministrator)) {
    if (Test-PortProxyReady) {
        Write-LanUrls
        exit 0
    }

    Write-Output "Windows administrator approval is needed once to expose WSL port $Port to the LAN."
    $scriptPath = $MyInvocation.MyCommand.Path
    $arguments = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', "`"$scriptPath`"",
        '-Port', $Port,
        '-WslAddress', $WslAddress,
        '-FirewallProfile', $FirewallProfile
    )
    $process = Start-Process powershell.exe -Verb RunAs -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0) {
        throw "Elevated LAN configuration failed with exit code $($process.ExitCode)."
    }
    if (-not (Test-PortProxyReady)) {
        throw 'Elevated LAN configuration returned without applying the expected firewall/forwarding state.'
    }
    Write-LanUrls
    exit 0
}

Set-Service -Name iphlpsvc -StartupType Automatic
Start-Service -Name iphlpsvc

$lanAddresses = @(Get-LanAddresses)
if ($lanAddresses.Count -eq 0) {
    throw 'Could not determine a physical Windows LAN address for WSL forwarding.'
}

# A wildcard portproxy also captures its own WSL destination on some WSL
# networking configurations, recursively opening connections until the port is
# unusable. Bind the proxy only to physical Windows LAN addresses instead.
$existingRows = @(Get-PortProxyRows)
foreach ($row in $existingRows) {
    if ($row.ListenPort -eq $Port -and $row.ConnectPort -eq $Port) {
        & netsh interface portproxy delete v4tov4 `
            listenaddress=$($row.ListenAddress) listenport=$Port 2>$null | Out-Null
    }
}
& netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$Port 2>$null | Out-Null

if ($lanAddresses -contains $WslAddress) {
    Write-Output "WSL mirrored networking detected; no portproxy is required."
} else {
    foreach ($address in $lanAddresses) {
        & netsh interface portproxy add v4tov4 `
            listenaddress=$address listenport=$Port connectaddress=$WslAddress connectport=$Port | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "netsh could not create the WSL port-forward for ${address}:$Port."
        }
    }
}

Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
$profiles = if ($FirewallProfile -eq 'Any') { 'Any' } else { $FirewallProfile -split ',' }
New-NetFirewallRule `
    -DisplayName $ruleName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $Port `
    -RemoteAddress LocalSubnet `
    -Profile $profiles | Out-Null

Write-LanUrls
