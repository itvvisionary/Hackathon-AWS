# Build the Lambda layer zip from the already-installed python/ directory.
# boto3/botocore are bundled in the AWS Lambda Python 3.12 runtime, so they are
# removed here to keep the layer small and under the 250 MB unzipped limit.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # suppress Compress-Archive progress spam
Set-Location -Path $PSScriptRoot

if (-not (Test-Path 'python')) {
    throw "python/ directory not found. Run the docker pip install step first."
}

# Remove runtime-bundled packages (the boto3 stack ships in the Lambda runtime).
# Keep requests' own deps (urllib3, idna, certifi, charset_normalizer, six) since
# they are not guaranteed in the runtime and are small.
$bundled = @('boto3', 'botocore', 's3transfer', 'jmespath')
foreach ($name in $bundled) {
    Get-ChildItem -Path 'python' -Force | Where-Object { $_.Name -like "$name*" } | ForEach-Object {
        Remove-Item -Recurse -Force -LiteralPath $_.FullName
        Write-Host "removed $($_.Name)"
    }
}

# Strip caches and compiled files
Get-ChildItem -Path 'python' -Recurse -Directory -Filter '__pycache__' -Force | Remove-Item -Recurse -Force
Get-ChildItem -Path 'python' -Recurse -Include '*.pyc', '*.pyo' -Force | Remove-Item -Force

$size = (Get-ChildItem -Path 'python' -Recurse -Force | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ("layer unzipped size: {0:N1} MB" -f $size)

# Zip it
if (Test-Path 'layer.zip') { Remove-Item -Force 'layer.zip' }
Compress-Archive -Path 'python' -DestinationPath 'layer.zip' -CompressionLevel Optimal
$zipMb = (Get-Item 'layer.zip').Length / 1MB
Write-Host ("layer.zip created: {0:N1} MB" -f $zipMb)
