param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$results = Join-Path $ProjectRoot 'report\allure-results'
$report = Join-Path $ProjectRoot 'report\allure-report'

if (-not (Test-Path -LiteralPath $results)) {
    throw "Allure results not found: $results. Run pytest first."
}

& allure generate $results --clean -o $report
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "Allure report generated: $report\index.html"
