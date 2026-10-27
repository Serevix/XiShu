param([string]$InputPath, [string]$OutputPdf)
$ErrorActionPreference = 'Stop'
$taskWord = $null
$taskDocument = $null
try {
    $taskWord = New-Object -ComObject Word.Application
    if ($taskWord.Documents.Count -ne 0) { throw '新建的自动化实例不是空实例，停止以避免影响其他文档。' }
    $taskWord.Visible = $false
    $taskWord.DisplayAlerts = 0
    $taskDocument = $taskWord.Documents.Open($InputPath, $false, $true, $false)
    $taskDocument.Repaginate()
    $taskDocument.ExportAsFixedFormat($OutputPdf, 17)
    Write-Output ('Pages=' + $taskDocument.ComputeStatistics(2))
} finally {
    if ($taskDocument) { $taskDocument.Close(0) }
    if ($taskWord -and $taskWord.Documents.Count -eq 0) { $taskWord.Quit(0) }
    if ($taskDocument) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($taskDocument) }
    if ($taskWord) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($taskWord) }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
