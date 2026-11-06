param([ValidateSet('Run','Enable','Disable','Status')][string]$Action='Status',[switch]$SelfTest)
$ErrorActionPreference='Stop'
$state=Join-Path $env:LOCALAPPDATA 'Kleinanzeiger'
$db=Join-Path $state 'anzeigen.sqlite'
$python=Join-Path $state '.venv\Scripts\python.exe'
$target=Join-Path $state 'Berichte'
$installed=Join-Path $state 'App\windows-weekly-report.ps1'
$name='Kleinanzeiger-Wochenbericht'
$argsExact='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$installed+'" -Action Run'
function Assert-Installed {
 if (!(Test-Path -LiteralPath $db -PathType Leaf) -or !(Test-Path -LiteralPath $python -PathType Leaf)) {
   throw 'Kleinanzeiger zuerst einrichten und lokale Anzeigen bereitstellen.'
 }
}
function Get-OwnedTask {
 $task=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
 if ($null -ne $task) {
   $actions=@($task.Actions)
   if ($actions.Count -ne 1 -or [IO.Path]::GetFileName([string]$actions[0].Execute) -ine 'powershell.exe' -or [string]$actions[0].Arguments -cne $argsExact) {
     throw 'Aufgabenname belegt; keine fremden Aufgaben veraendern.'
   }
 }
 return $task
}
if ($SelfTest) {
 if ($argsExact -notmatch '-Action Run$') { throw 'Ungültige Aufrufplanung.' }
 Write-Output 'WINDOWS_WEEKLY_SELFTEST_OK'
 return
}
switch ($Action) {
 'Run' {
   Assert-Installed
   & $python -m mark_api.weekly_report --db $db --output-dir $target
   if ($LASTEXITCODE -ne 0) { throw 'Wochenbericht nicht verfuegbar.' }
   Write-Output 'WEEKLY_REPORT_RUN_OK'
 }
 'Status' { Write-Output $(if (Get-OwnedTask) {'WEEKLY_ENABLED'} else {'WEEKLY_DISABLED'}) }
 'Enable' {
   Assert-Installed
   if (!(Test-Path -LiteralPath $installed -PathType Leaf)) { throw 'Berichtsskript fehlt in installierter Anwendung.' }
   [void](Get-OwnedTask)
   $taskAction=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argsExact
   $trigger=New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At '09:00'
   $principal=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
   $settings=New-ScheduledTaskSettingsSet -StartWhenAvailable
   Register-ScheduledTask -TaskName $name -Action $taskAction -Trigger $trigger -Principal $principal -Settings $settings -Description 'Opt-in-Bericht montags 09:00 bei Anmeldung' -Force | Out-Null
   [void](Get-OwnedTask)
   Write-Output 'WEEKLY_ENABLED'
 }
 'Disable' {
   $old=Get-OwnedTask
   if ($old) { Unregister-ScheduledTask -TaskName $name -Confirm:$false }
   Write-Output 'WEEKLY_DISABLED'
 }
}
