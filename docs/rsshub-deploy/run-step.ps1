param([Parameter(Mandatory=$true)][string]$CommandFile,[Parameter(Mandatory=$true)][string]$Title)
$ErrorActionPreference = 'Stop'
$record = 'D:\notebooks\info-agent\docs\RSSHub部署记录.md'
$remote = [IO.File]::ReadAllText($CommandFile).Replace("`r`n", "`n")
$intro = "`n## $Title`n`n执行时间：$([DateTimeOffset]::Now.ToString('o'))`n`n" + '```bash' + "`n" + $remote + "`n" + '```' + "`n"
Add-Content -LiteralPath $record -Value $intro -Encoding utf8
$raw = & 'D:\Tools\workbench\workbench.exe' exec -i i-2zebyfc3w77fj4edjmgb -r cn-beijing -P astrbot -o json --timeout 30 -c $remote
$exitCode = $LASTEXITCODE
try {
    $result = ($raw -join "`n") | ConvertFrom-Json
    $output = "远端退出码：$($result.exit_code)`n$($result.stdout)`n$($result.stderr)"
} catch { $output = "CLI 退出码：$exitCode`n" + ($raw -join "`n") }
Add-Content -LiteralPath $record -Value ('```text' + "`n" + $output + "`n" + '```' + "`n") -Encoding utf8
Write-Output $output
