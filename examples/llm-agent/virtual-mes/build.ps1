#Requires -Version 5.1
<#
.SYNOPSIS
    Builds the virtual MES app into a runnable WinForms exe.

.DESCRIPTION
    Compiles every .cs file in this directory as one module with the in-box
    .NET Framework compiler, resolved by absolute path so the build cannot
    pick up a different compiler through PATH. The language level is pinned
    at /langversion:5, the highest this compiler accepts.

    Output is written to a temporary path first and moved into place only
    after the compile succeeds, so a failed build keeps the previous exe.

.PARAMETER OutputDir
    Directory VirtualMes.exe is written into. Defaults to "build" beside
    this script.

.OUTPUTS
    Writes "Built: <path>" on success. Throws on any failure.
#>
[CmdletBinding()]
param(
    [string]$OutputDir
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$here = Split-Path -Parent $PSCommandPath
if (-not $OutputDir) {
    $OutputDir = Join-Path $here 'build'
}

$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $csc)) {
    throw ("compiler not found at " + $csc + " - this app targets the in-box .NET Framework 4.x csc.exe")
}

# The UIAutomation assemblies and WindowsBase ship only in the GAC, and the
# in-box compiler does not search the GAC for a bare file name.
function Resolve-GacAssembly {
    param([Parameter(Mandatory = $true)][string]$AssemblyName)
    $root = Join-Path $env:WINDIR ('Microsoft.NET\assembly\GAC_MSIL\' + $AssemblyName)
    if (-not (Test-Path -LiteralPath $root)) {
        throw ("required GAC assembly not found: " + $AssemblyName + " (looked under " + $root + ")")
    }
    $found = Get-ChildItem -LiteralPath $root -Recurse -Filter ($AssemblyName + '.dll') |
        Select-Object -First 1 -ExpandProperty FullName
    if (-not $found) {
        throw ("required GAC assembly not found: " + $AssemblyName + " (searched " + $root + ")")
    }
    return $found
}

$uiaProvider = Resolve-GacAssembly -AssemblyName 'UIAutomationProvider'
$uiaTypes = Resolve-GacAssembly -AssemblyName 'UIAutomationTypes'
$windowsBase = Resolve-GacAssembly -AssemblyName 'WindowsBase'

if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

$exePath = Join-Path $OutputDir 'VirtualMes.exe'
$tempExePath = Join-Path $OutputDir ('VirtualMes.exe.build-' + [guid]::NewGuid().ToString('N'))

$sources = @(Get-ChildItem -LiteralPath $here -Filter '*.cs' | Select-Object -ExpandProperty FullName)
if ($sources.Count -eq 0) {
    throw ("no .cs source files found in " + $here)
}

$cscArgs = @(
    '/nologo',
    '/target:winexe',
    '/langversion:5',
    '/platform:anycpu',
    '/warnaserror',
    ('/out:' + $tempExePath),
    '/reference:System.dll',
    '/reference:System.Core.dll',
    '/reference:System.Drawing.dll',
    '/reference:System.Windows.Forms.dll',
    '/reference:System.IO.Compression.dll',
    '/reference:System.Web.Extensions.dll',
    ('/reference:' + $uiaProvider),
    ('/reference:' + $uiaTypes),
    ('/reference:' + $windowsBase)
) + $sources

$output = @(& $csc $cscArgs 2>&1 | ForEach-Object { "$_" })
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0 -or -not (Test-Path -LiteralPath $tempExePath)) {
    Remove-Item -LiteralPath $tempExePath -Force -ErrorAction SilentlyContinue
    throw ("csc.exe failed with exit code " + $exitCode + "`n" + ($output -join [Environment]::NewLine))
}

Move-Item -LiteralPath $tempExePath -Destination $exePath -Force

Write-Output ("Built: " + $exePath)
