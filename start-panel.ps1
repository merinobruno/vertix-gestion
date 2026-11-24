$ErrorActionPreference = 'Stop'
$ports = @(8765, 8766)
foreach ($port in $ports) {
    $url = "http://127.0.0.1:$port"
    try {
        $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 2
        if ($health.service -eq 'panel-documental' -and $health.annex_import -eq $true -and
            $health.directory_editor -eq $true -and $health.identification_formats -eq $true) {
            Start-Process "$url/"
            exit 0
        }
    } catch { }
}
$available = $null
foreach ($port in $ports) {
    $socket = [Net.Sockets.TcpClient]::new()
    try { $socket.Connect('127.0.0.1', $port) }
    catch [Net.Sockets.SocketException] { $available = $port; break }
    finally { $socket.Dispose() }
}
if (-not $available) {
    Write-Host 'Los puertos locales 8765 y 8766 ya estan ocupados.'
    exit 1
}
$python = Get-AppxPackage -Name 'OpenAI.CodexPrimaryRuntime*' |
    Sort-Object { [version]$_.Version } -Descending |
    ForEach-Object { Join-Path $_.InstallLocation 'dependencies\python\python.exe' } |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) {
    Write-Host 'Se requiere el entorno local de Python con lxml y openpyxl.'
    exit 1
}
& $python -B (Join-Path $PSScriptRoot 'server.py') --port $available --open
exit $LASTEXITCODE
