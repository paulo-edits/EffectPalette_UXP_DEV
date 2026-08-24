# Ensures the 16 cmd.edit.label.N commands plus cmd.edit.labelgroup and cmd.clip.nestify have a
# keyboard shortcut bound in the user's active Premiere Pro keyboard profile (.kys), assigning an
# obscure internal one (Ctrl+Alt+Shift+<letter/digit/F13-F24>) to whichever of those commands don't
# already have one. Timeline-clip Label/Label-group has no DOM API on either CEP or UXP - the real
# product (companion/app.py's find_premiere_command_shortcut/send_native_shortcut) always worked by
# resolving one of these commands out of the .kys file and synthesizing the matching keystroke, so
# this script exists purely to guarantee that keystroke exists to resolve in the first place.
#
# Ported from the stable CEP product's packaging/runtime/configure_premiere.ps1 (read-only reference,
# never modified) - same logic, adapted only in how it locates a log destination, since this repo has
# no installer/InstallDir concept yet (it runs from source, not from an installed location).

param(
    [string]$LogDir = (Join-Path $PSScriptRoot "..\companion\data"),
    [string]$PremiereRoot = ""
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$logFile = Join-Path $LogDir "premiere_shortcut_configuration.log"

function Write-InstallLog([string]$Message) {
    $line = "{0:u} {1}" -f (Get-Date), $Message
    Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
    Write-Host $Message
}

function New-ShortcutCandidate([int]$Vk, [bool]$Ctrl, [bool]$Alt, [bool]$Shift) {
    return [pscustomobject]@{ Vk = $Vk; Ctrl = $Ctrl; Alt = $Alt; Shift = $Shift }
}

function Get-ShortcutKey($Candidate) {
    return "{0}:{1}:{2}:{3}" -f $Candidate.Vk, $Candidate.Ctrl, $Candidate.Alt, $Candidate.Shift
}

try {
    Write-InstallLog "Starting Premiere profile configuration."
    if (-not $PremiereRoot) {
        $PremiereRoot = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "Adobe\Premiere Pro"
    }
    $kysFile = Get-ChildItem -LiteralPath $PremiereRoot -Filter "*.kys" -File -Recurse -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1

    if (-not $kysFile) {
        Write-InstallLog "No Premiere .kys profile found. Launch Premiere once, create or select a keyboard preset, then run this script again."
        exit 0
    }

    [xml]$xml = Get-Content -LiteralPath $kysFile.FullName -Raw -Encoding UTF8
    $globalContext = $xml.SelectSingleNode("//context.global")
    if (-not $globalContext) {
        Write-InstallLog "The newest .kys file has no context.global node: $($kysFile.FullName)"
        exit 0
    }

    $existingCommands = @{}
    $usedShortcuts = @{}
    $highestItem = 0
    foreach ($node in $globalContext.ChildNodes) {
        if ($node.Name -match '^item\.(\d+)$') {
            $highestItem = [Math]::Max($highestItem, [int]$Matches[1])
        }
        $command = [string]$node.commandname
        if ($command) {
            $existingCommands[$command] = $true
        }
        [int64]$rawVk = 0
        if ([int64]::TryParse([string]$node.virtualkey, [ref]$rawVk)) {
            $candidate = New-ShortcutCandidate -Vk ([int]($rawVk -band 0xFFFF)) `
                -Ctrl ([string]$node.'modifier.ctrl' -eq "true") `
                -Alt ([string]$node.'modifier.alt' -eq "true") `
                -Shift ([string]$node.'modifier.shift' -eq "true")
            $usedShortcuts[(Get-ShortcutKey $candidate)] = $command
        }
    }

    $commands = @("cmd.clip.nestify")
    0..15 | ForEach-Object { $commands += "cmd.edit.label.$_" }
    $commands += "cmd.edit.labelgroup"

    $candidatePool = New-Object System.Collections.Generic.List[object]
    # Keep generated bindings obscure enough not to collide with normal editing.
    foreach ($char in ([char[]]"PQRSTUVWXYZABCDEFGHIJKLM")) {
        $candidatePool.Add((New-ShortcutCandidate -Vk ([int][char]$char) -Ctrl $true -Alt $true -Shift $true))
    }
    foreach ($digit in 1..9) {
        $candidatePool.Add((New-ShortcutCandidate -Vk (0x30 + $digit) -Ctrl $true -Alt $true -Shift $true))
    }
    foreach ($vk in 0x7C..0x87) {
        $candidatePool.Add((New-ShortcutCandidate -Vk $vk -Ctrl $true -Alt $true -Shift $true))
    }

    $added = 0
    foreach ($command in $commands) {
        if ($existingCommands.ContainsKey($command)) {
            Write-InstallLog "Kept existing Premiere command binding: $command"
            continue
        }
        $candidate = $candidatePool | Where-Object { -not $usedShortcuts.ContainsKey((Get-ShortcutKey $_)) } | Select-Object -First 1
        if (-not $candidate) {
            Write-InstallLog "No free internal shortcut candidate for: $command"
            continue
        }

        $highestItem++
        $item = $xml.CreateElement("item.$highestItem")
        $item.SetAttribute("Version", "1")
        $values = [ordered]@{
            "virtualkey" = ([int64]0x80000000 + [int64]$candidate.Vk).ToString()
            "modifier.ctrl" = $candidate.Ctrl.ToString().ToLowerInvariant()
            "modifier.alt" = $candidate.Alt.ToString().ToLowerInvariant()
            "modifier.shift" = $candidate.Shift.ToString().ToLowerInvariant()
            "commandname" = $command
        }
        foreach ($entry in $values.GetEnumerator()) {
            $child = $xml.CreateElement($entry.Key)
            $child.InnerText = $entry.Value
            [void]$item.AppendChild($child)
        }
        [void]$globalContext.AppendChild($item)
        $usedShortcuts[(Get-ShortcutKey $candidate)] = $command
        $existingCommands[$command] = $true
        $added++
        Write-InstallLog "Added internal Premiere binding: $command"
    }

    if ($added -gt 0) {
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $backup = "$($kysFile.FullName).fxpalette-installer-$stamp.bak"
        Copy-Item -LiteralPath $kysFile.FullName -Destination $backup -Force
        $settings = New-Object System.Xml.XmlWriterSettings
        $settings.Indent = $true
        $settings.Encoding = New-Object System.Text.UTF8Encoding($false)
        $writer = [System.Xml.XmlWriter]::Create($kysFile.FullName, $settings)
        try { $xml.Save($writer) } finally { $writer.Dispose() }
        Write-InstallLog "Added $added bindings to $($kysFile.FullName). Backup: $backup"
    } else {
        Write-InstallLog "Premiere profile already configured; no .kys changes required."
    }
} catch {
    Write-InstallLog "Configuration failed: $($_.Exception.Message)"
    exit 0
}

exit 0
