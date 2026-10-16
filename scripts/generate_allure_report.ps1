param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$results = Join-Path $ProjectRoot 'allure-results'
$report = Join-Path $ProjectRoot 'report\allure-report'

if (-not (Test-Path -LiteralPath $results)) {
    throw "未找到 Allure 原始结果：$results。请先在 Ubuntu 运行 pytest。"
}

allure generate $results --clean -o $report
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "报告已生成：$report\index.html"
