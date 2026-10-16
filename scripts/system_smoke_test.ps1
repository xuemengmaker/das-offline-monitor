param(
    [string]$BaseUrl = 'http://127.0.0.1:8000',
    [int]$TimeoutSeconds = 30
)

$health = Invoke-RestMethod -Uri "$BaseUrl/api/v1/health"
if ($health.status -ne 'ok' -or -not $health.data_root_available) {
    throw '服务未就绪，或 Linux 数据根目录不可用。请先启动 ./scripts/run_dev.sh。'
}

$file = (Invoke-RestMethod -Uri "$BaseUrl/api/v1/tdms/files?limit=1").files[0]
if ($null -eq $file) {
    throw '未发现 TDMS 文件。'
}

$payload = @{
    relative_path       = $file.relative_path
    start_sample        = 0
    n_samples           = 1000
    channel_start       = 1
    channel_end         = 24
    background_fraction = 0.60
} | ConvertTo-Json

$job = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/analysis-jobs" -ContentType 'application/json' -Body $payload
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
do {
    Start-Sleep -Milliseconds 500
    $job = Invoke-RestMethod -Uri "$BaseUrl/api/v1/analysis-jobs/$($job.job_id)"
} while ($job.status -in @('queued', 'running') -and (Get-Date) -lt $deadline)

if ($job.status -ne 'completed') {
    throw "系统冒烟测试失败：job_id=$($job.job_id), status=$($job.status), error=$($job.error)"
}

[pscustomobject]@{
    job_id          = $job.job_id
    status          = $job.status
    relative_path   = $file.relative_path
    candidate_count = $job.result.summary.candidate_count
    median_rms      = $job.result.summary.median_rms
} | Format-List
