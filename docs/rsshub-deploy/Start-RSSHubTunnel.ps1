$ErrorActionPreference = 'Stop'
$port = 1200
$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($listener) { throw '本机 1200 端口已占用，请先确认占用程序。' }
$helper = 'D:\Tools\workbench\helpers\ali-instance-cli.exe'
$config = 'D:\Tools\workbench\private\ali-session-config.json'
Start-Process -FilePath $helper -ArgumentList @('portforward','-i','i-2zebyfc3w77fj4edjmgb','-r',"$port",'-l',"$port",'--profile','astrbot','--config-path',$config) -WorkingDirectory 'D:\Tools\workbench\helpers' -WindowStyle Hidden -RedirectStandardOutput 'D:\Tools\workbench\helpers\forward-rsshub.log' -RedirectStandardError 'D:\Tools\workbench\helpers\forward-rsshub.err'
Write-Output '转发已启动；连接建立后访问 http://127.0.0.1:1200/'
