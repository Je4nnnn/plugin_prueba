param([switch]$Reopen, [string]$InputProject = '', [string]$RunLabel = '', [string]$WorkspacePath = '')
$ErrorActionPreference = 'Stop'
$validationDir = $PSScriptRoot
if ($WorkspacePath) {
    $workspaceDir = [System.IO.Path]::GetFullPath($WorkspacePath)
} else {
    $candidateDir = Get-Item -LiteralPath $validationDir
    while ($candidateDir -and -not (Test-Path -LiteralPath (Join-Path $candidateDir.FullName 'EMBOX_LOCAL'))) { $candidateDir = $candidateDir.Parent }
    if (-not $candidateDir) { throw 'Indique WorkspacePath con la instalación EMBOX_LOCAL.' }
    $workspaceDir = $candidateDir.FullName
}
$installationDir = Join-Path $workspaceDir 'EMBOX_LOCAL'
$inputDir = Join-Path $workspaceDir 'plugin_prueba\salidas\demo_embox'
if ($RunLabel -and $RunLabel -notmatch '^[a-zA-Z0-9_]+$') { throw 'RunLabel sólo admite letras, números y guion bajo.' }
$artifactDir = if ($RunLabel) { Join-Path $validationDir $RunLabel } else { $validationDir }
$copyDir = Join-Path $artifactDir 'copia_proyecto'
$profileDir = Join-Path $artifactDir 'perfil_aislado'
$catalogDir = Join-Path $profileDir '.EMCoder'
$javaBin = 'C:\Program Files\Eclipse Adoptium\jdk-21.0.10.7-hotspot\bin'
$originalProject = if ($InputProject) { [System.IO.Path]::GetFullPath($InputProject) } else { Join-Path $inputDir 'project.json' }
$hashBefore = (Get-FileHash -LiteralPath $originalProject -Algorithm SHA256).Hash
New-Item -ItemType Directory -Force -Path $copyDir,$catalogDir | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding($false)
if (-not $Reopen) {
    Copy-Item -LiteralPath $originalProject -Destination (Join-Path $copyDir 'project.json') -Force
    $project = Get-Content -LiteralPath $originalProject -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($source in $project.sources) {
        $sourceOriginal = $source.pathToSource
        $sourceCopy = Join-Path $copyDir ([System.IO.Path]::GetFileName($sourceOriginal))
        Copy-Item -LiteralPath $sourceOriginal -Destination $sourceCopy -Force
        $source.pathToSource = $sourceCopy
        foreach ($tag in $project.tags) {
            if ($tag.source -eq $sourceOriginal -and $tag.canal -eq $source.canal) { $tag.source = $sourceCopy }
        }
    }
    [System.IO.File]::WriteAllText((Join-Path $copyDir 'project.json'), ($project | ConvertTo-Json -Depth 30), $utf8)
    [System.IO.File]::WriteAllText((Join-Path $catalogDir 'proyectos.txt'), ($copyDir + [Environment]::NewLine), $utf8)
    [System.IO.File]::WriteAllText((Join-Path $catalogDir 'canales.json'), '[]', $utf8)
}
$runSuffix = if ($Reopen) { '_reapertura' } else { '' }
$libraryDir = Join-Path $installationDir 'app\lib\*'
$nativeDir = Join-Path $installationDir 'app\nativos'
$gstFile = Get-ChildItem -LiteralPath (Join-Path $installationDir 'gstreamer') -Filter 'gstreamer-1.0-0.dll' -Recurse -File | Select-Object -First 1
$gstBin = $gstFile.DirectoryName
$env:PATH = (Join-Path $installationDir 'python\Scripts') + ';' + $nativeDir + ';' + $gstBin + ';' + $env:PATH
$env:MPLBACKEND = 'TkAgg'
& (Join-Path $javaBin 'javac.exe') -encoding UTF-8 -cp $libraryDir -d $validationDir (Join-Path $validationDir 'VerificarProyecto.java')
if ($LASTEXITCODE -ne 0) { throw 'Falló compilación del harness.' }
$arguments = @(
    ('"-Duser.home=' + $profileDir + '"'),
    ('"-Djava.library.path=' + $nativeDir + ';' + $gstBin + '"'),
    ('"-Dgstreamer.path=' + $gstBin + '"'),
    '-Dfile.encoding=UTF-8',
    '-cp', ('"' + $libraryDir + ';' + $validationDir + '"'),
    'VerificarProyecto', ('"' + (Join-Path $copyDir 'project.json') + '"'),
    ('"' + (Join-Path $artifactDir ('resultado'+$runSuffix+'.json')) + '"'),
    ('"' + (Join-Path $artifactDir ('snapshot'+$runSuffix+'.png')) + '"')
)
$argsPath = Join-Path $artifactDir 'java-validacion.args'
$arguments = $arguments | ForEach-Object { $_.Replace('\','/') }
[System.IO.File]::WriteAllLines($argsPath, $arguments, $utf8)
$process = Start-Process -FilePath (Join-Path $javaBin 'java.exe') -ArgumentList ('@' + $argsPath) -WorkingDirectory (Join-Path $installationDir 'app') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $artifactDir ('salida'+$runSuffix+'.log')) -RedirectStandardError (Join-Path $artifactDir ('errores'+$runSuffix+'.log')) -PassThru
[System.IO.File]::WriteAllText((Join-Path $artifactDir 'pid_propio.txt'), [string]$process.Id, $utf8)
Write-Output ('HARNESS_PID=' + $process.Id)
Write-Output ('ORIGINAL_PROJECT_SHA256=' + $hashBefore)
Write-Output ('RESULTADO=' + (Join-Path $artifactDir ('resultado'+$runSuffix+'.json')))
