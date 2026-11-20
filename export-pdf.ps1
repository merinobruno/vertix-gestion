param([Parameter(Mandatory = $true)][string]$Directory)
$ErrorActionPreference = 'Stop'
$dir = [IO.Path]::GetFullPath($Directory)
$originalWordPids = @(Get-Process WINWORD -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class PanelWordWindow {
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
}
'@
$word = $null
$bootstrap = $null
$ownsWord = $false
try {
    $word = New-Object -ComObject Word.Application
    # Word exposes its HWND on Window, not Application. Open one of our own
    # documents read-only without creating a recent-item record or a visible window.
    $items = @(Get-ChildItem -LiteralPath $dir -File -Filter '*.docx')
    $source = $items[0].FullName
    $missing = [Type]::Missing
    $bootstrap = $word.Documents.Open($source, $false, $true, $false, $missing, $missing, $false, $missing, $missing, 0, $missing, $false)
    [uint32]$wordPid = 0
    [void][PanelWordWindow]::GetWindowThreadProcessId([IntPtr]$bootstrap.Windows.Item(1).Hwnd, [ref]$wordPid)
    if ($wordPid -le 0 -or $originalWordPids -contains $wordPid) {
        throw 'Could not isolate Word for this batch.'
    }
    $ownsWord = $true
    $process = Get-Process -Id $wordPid
    [IO.File]::WriteAllText((Join-Path $dir 'word-instance.txt'), "$wordPid $($process.StartTime.ToFileTimeUtc())")
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    foreach ($file in $items) {
        $document = $null
        try {
            if ($file.FullName -eq $source) {
                $document = $bootstrap
                $bootstrap = $null
            } else {
                $document = $word.Documents.Open($file.FullName, $false, $true, $false)
            }
            $pdf = [IO.Path]::ChangeExtension($file.FullName, '.pdf')
            # Format PDF, document content only, all pages, omit Office metadata.
            $document.ExportAsFixedFormat($pdf, 17, $false, 0, 0, 1, 1, 0, $false, $false, 0, $true, $true, $false)
        } finally {
            if ($document) {
                try { $document.Close(0) } finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document) }
            }
        }
    }
} finally {
    if ($bootstrap) {
        try { $bootstrap.Close(0) } finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($bootstrap) }
    }
    if ($word) {
        try { if ($ownsWord) { $word.Quit() } }
        finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($word) }
    }
}
