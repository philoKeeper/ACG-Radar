# Build ACG Radar 1.0.0 portable release.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$version = (Get-Content (Join-Path $root 'VERSION') -Raw).Trim()
$stage = Join-Path $root ('dist\ACG-Radar-' + $version)
$zip = Join-Path $root ('dist\ACG-Radar-' + $version + '-win64.zip')

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
if (Test-Path $zip) { Remove-Item $zip -Force }
New-Item -ItemType Directory -Path $stage -Force | Out-Null

$files = @('app.py', 'start.bat', 'README.md', 'README-FIRST.txt', 'CHANGELOG.md', 'VERSION', '.gitignore')
foreach ($file in $files) {
  Copy-Item (Join-Path $root $file) $stage
}
foreach ($dir in @('backend', 'web', 'tests')) {
  Copy-Item (Join-Path $root $dir) (Join-Path $stage $dir) -Recurse
}
Get-ChildItem $stage -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
Get-ChildItem $stage -Recurse -File -Include '*.pyc', '*.pyo' | Remove-Item -Force
if (Test-Path (Join-Path $stage 'data')) { Remove-Item (Join-Path $stage 'data') -Recurse -Force }

Compress-Archive -Path $stage -DestinationPath $zip -CompressionLevel Optimal
Write-Host ('Built ' + $zip)
