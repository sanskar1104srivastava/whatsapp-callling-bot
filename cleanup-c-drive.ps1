#Requires -RunAsAdministrator
# One-off: free space on C: (Docker cache + dev caches) and shrink Docker's disk image so Windows gets the space back.
# Run in an ADMIN PowerShell:   powershell -ExecutionPolicy Bypass -File D:\vriddhiVoiceCalling\cleanup-c-drive.ps1
# What it removes: Docker build cache, stopped containers, unused networks, ALL images not used by a running container
# (they are re-pulled / rebuilt when needed), the uv and npm download caches (re-downloaded when needed).
# What it keeps: Docker volumes (database data etc.), running containers, all your files.

$ErrorActionPreference = "Continue"
$user = "C:\Users\Sanskar"
$vhd = "$user\AppData\Local\Docker\wsl\disk\docker_data.vhdx"
function Show-Free { "C: free: {0:N2} GB" -f ((Get-PSDrive C).Free / 1GB) }
Show-Free

# 1) a little room first, so Docker Desktop can start at all (C: was at 0 MB)
Write-Host "`n[1/3] Clearing uv and npm caches..."
if (Get-Command uv -ErrorAction SilentlyContinue) { uv cache clean }
if (Get-Command npm -ErrorAction SilentlyContinue) { npm cache clean --force }
Show-Free

# 2) start Docker Desktop, wait for the engine, prune
Write-Host "`n[2/3] Starting Docker Desktop and pruning..."
$dd = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
if (Test-Path $dd) { Start-Process $dd }
$deadline = (Get-Date).AddMinutes(4)
do { Start-Sleep -Seconds 5; docker info *> $null } until ($LASTEXITCODE -eq 0 -or (Get-Date) -gt $deadline)
if ($LASTEXITCODE -eq 0) {
    docker system df
    docker builder prune --all --force
    docker system prune --all --force      # no --volumes: volumes are kept
    docker system df
} else {
    Write-Warning "Docker engine did not start; skipping prune (the disk image is still compacted below)."
}

# 3) shrink docker_data.vhdx: pruning frees space inside the image, but the file only shrinks when compacted
Write-Host "`n[3/3] Compacting Docker's disk image..."
Get-Process "Docker Desktop", "com.docker.backend", "com.docker.build" -ErrorAction SilentlyContinue | Stop-Process -Force
wsl --shutdown
Start-Sleep -Seconds 5
if (Test-Path $vhd) {
    "before: {0:N2} GB" -f ((Get-Item $vhd).Length / 1GB)
    if (Get-Command Optimize-VHD -ErrorAction SilentlyContinue) {
        Optimize-VHD -Path $vhd -Mode Full
    } else {
        $script = "D:\vriddhiVoiceCalling\_compact_vhdx.txt"   # on D: because C: may still be nearly full
        "select vdisk file=`"$vhd`"`r`nattach vdisk readonly`r`ncompact vdisk`r`ndetach vdisk`r`nexit" | Out-File -Encoding ascii $script
        diskpart /s $script
        Remove-Item $script -ErrorAction SilentlyContinue
    }
    "after:  {0:N2} GB" -f ((Get-Item $vhd).Length / 1GB)
} else {
    Write-Warning "Docker disk image not found at $vhd"
}
Show-Free
Write-Host "`nDone. Start Docker Desktop again whenever you need it."
