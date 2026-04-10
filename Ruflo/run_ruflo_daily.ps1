$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Set-Location $root
.\run_ruflo.ps1 -Headless
