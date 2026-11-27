# Counts the UI Automation framework ids and supported pattern names under one window.
# Prints one JSON line: {"nodes":..,"truncated":..,"frameworks":{..},"patterns":{..}}.
# It reads only each element's framework id (a toolkit name) and its supported patterns,
# never a name, an id, a value or any other text of the window.
param(
    [Parameter(Mandatory = $true)][long]$Hwnd,
    [int]$MaxNodes = 3000
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Hwnd)
$walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
$frameworks = @{}
$patterns = @{}
$nodes = 0
$truncated = $false
$queue = New-Object 'System.Collections.Generic.Queue[System.Windows.Automation.AutomationElement]'
$queue.Enqueue($root)

while ($queue.Count -gt 0) {
    if ($nodes -ge $MaxNodes) { $truncated = $true; break }
    $element = $queue.Dequeue()
    $nodes++
    $framework = ''
    try { $framework = [string]$element.Current.FrameworkId } catch { $framework = '' }
    if ([string]::IsNullOrEmpty($framework)) { $framework = '(none)' }
    $frameworks[$framework] = 1 + [int]$frameworks[$framework]
    try {
        foreach ($pattern in $element.GetSupportedPatterns()) {
            $label = [string]$pattern.ProgrammaticName
            $cut = $label.IndexOf('Pattern')
            if ($cut -gt 0) { $label = $label.Substring(0, $cut) }
            $patterns[$label] = 1 + [int]$patterns[$label]
        }
    } catch { }
    $child = $null
    try { $child = $walker.GetFirstChild($element) } catch { $child = $null }
    while ($null -ne $child) {
        $queue.Enqueue($child)
        try { $child = $walker.GetNextSibling($child) } catch { $child = $null }
    }
}

@{ nodes = $nodes; truncated = $truncated; frameworks = $frameworks; patterns = $patterns } |
    ConvertTo-Json -Compress -Depth 3
