Add-Type -AssemblyName System.IO.Compression.FileSystem

$root    = (Get-Location).Path
$zipPath = Join-Path $root 'phase2_current.zip'
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

$excludeDirs = @('\.venv\', '\.idea\', '__pycache__\', '\.git\')
$excludeExt  = @('.gguf', '.zip', '.pyc')

$files = Get-ChildItem -Path $root -Recurse -File | Where-Object {
    $rel = '\' + $_.FullName.Substring($root.Length).TrimStart('\')
    if ($_.Name -eq '.env') { return $false }
    if ($excludeExt -contains $_.Extension.ToLower()) { return $false }
    foreach ($d in $excludeDirs) {
        if ($rel.Contains($d)) { return $false }
    }
    return $true
}

$zip = [System.IO.Compression.ZipFile]::Open($zipPath, 'Create')
try {
    foreach ($f in $files) {
        $entryName = $f.FullName.Substring($root.Length).TrimStart('\')
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $zip, $f.FullName, $entryName
        ) | Out-Null
    }
} finally {
    $zip.Dispose()
}

Write-Host "Done: $zipPath ($($files.Count) files)"