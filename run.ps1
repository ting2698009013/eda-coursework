param(
    [ValidateSet('doctor','test','smoke','study','report','extension-report','demo','bootstrap')]
    [string]$Action = 'doctor',
    [string]$Run = '',
    [switch]$Resume,
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$projectDirectory = $PSScriptRoot
Push-Location -LiteralPath $projectDirectory
try {
    if ($Action -eq 'bootstrap') {
        & $Python -X utf8 scripts/bootstrap.py
    } elseif ($Action -eq 'report') {
        if (-not $Run) { $Run = 'results/study_20261009_v2' }
        & $Python -X utf8 -m lutsearch report --run $Run
    } elseif ($Action -eq 'extension-report') {
        if (-not $Run) { $Run = 'results/extension_20261010' }
        & $Python -X utf8 scripts/campaign_report.py $Run
    } elseif ($Action -eq 'test') {
        & wsl -d Ubuntu --cd $projectDirectory --exec python3 -m unittest discover -s tests -v
    } elseif ($Action -eq 'demo') {
        if (-not $Run) { $Run = 'results/demo-' + (Get-Date -Format 'yyyyMMdd-HHmmss') }
        & wsl -d Ubuntu --cd $projectDirectory --exec python3 -m lutsearch search --input benchmarks/int2float.aig --out $Run --method lut_beam --calls 80
    } elseif ($Action -in @('smoke','study')) {
        if (-not $Run) { $Run = 'results/' + $Action + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss') }
        $arguments = @('-d','Ubuntu','--cd',$projectDirectory,'--exec','python3','-m','lutsearch','run','--config',('configs/'+$Action+'.json'),'--out',$Run)
        if ($Resume) { $arguments += '--resume' }
        & wsl @arguments
    } else {
        & wsl -d Ubuntu --cd $projectDirectory --exec python3 -m lutsearch doctor
    }
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}
