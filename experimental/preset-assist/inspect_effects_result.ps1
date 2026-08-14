param(
    [Parameter(Mandatory = $true)][string]$ImagePath,
    [Parameter(Mandatory = $true)][string]$ExpectedQuery,
    [switch]$MovePointer,
    [ValidateRange(1, 30)][int]$MaxImageAgeSeconds = 5
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.FileAccessMode, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]

function Await-WinRT($Operation, [Type]$ResultType) {
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object { $_.Name -eq "AsTask" -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 } |
        Select-Object -First 1
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.Wait()
    return $task.Result
}

function Normalize-Text([string]$Text) {
    return (($Text -replace "\s+", " ").Trim()).ToUpperInvariant()
}

function Rect-Object($Rect) {
    return [ordered]@{ left = $Rect.X; top = $Rect.Y; width = $Rect.Width; height = $Rect.Height }
}

function Ensure-CursorApi {
    if (-not ("FxPalette.NativeCursor" -as [type])) {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace FxPalette {
    public static class NativeCursor {
        [StructLayout(LayoutKind.Sequential)]
        public struct Point { public int X; public int Y; }
        [DllImport("user32.dll")] public static extern bool GetCursorPos(out Point point);
        [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    }
}
"@
    }
}

try {
    $premiere = Get-Process -Name "Adobe Premiere Pro" | Where-Object { $_.MainWindowHandle -ne 0 }
    if (@($premiere).Count -ne 1) { throw "Expected exactly one Premiere process with a main window." }
    $premiere = @($premiere)[0]
    $root = [System.Windows.Automation.AutomationElement]::FromHandle($premiere.MainWindowHandle)
    $descendants = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    $expected = Normalize-Text $ExpectedQuery
    $searchElements = @()
    for ($index = 0; $index -lt $descendants.Count; $index++) {
        $element = $descendants.Item($index)
        if ($element.Current.ClassName -eq "Edit" -and (Normalize-Text $element.Current.Name) -eq $expected -and -not $element.Current.IsOffscreen) {
            $searchElements += $element
        }
    }
    if ($searchElements.Count -ne 1) { throw "Expected exactly one visible Edit with the exact query; found $($searchElements.Count)." }
    $searchRect = $searchElements[0].Current.BoundingRectangle
    $windowRect = $root.Current.BoundingRectangle

    $file = Await-WinRT ([Windows.Storage.StorageFile]::GetFileFromPathAsync((Resolve-Path -LiteralPath $ImagePath).Path)) ([Windows.Storage.StorageFile])
    $stream = Await-WinRT ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await-WinRT ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if (-not $engine) { throw "No Windows OCR engine is available for the installed user languages." }
    $ocr = Await-WinRT ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

    $matches = @()
    foreach ($line in $ocr.Lines) {
        if ((Normalize-Text $line.Text) -ne $expected) { continue }
        $words = @($line.Words)
        if (-not $words.Count) { continue }
        $left = ($words | ForEach-Object { $_.BoundingRect.X } | Measure-Object -Minimum).Minimum
        $top = ($words | ForEach-Object { $_.BoundingRect.Y } | Measure-Object -Minimum).Minimum
        $right = ($words | ForEach-Object { $_.BoundingRect.X + $_.BoundingRect.Width } | Measure-Object -Maximum).Maximum
        $bottom = ($words | ForEach-Object { $_.BoundingRect.Y + $_.BoundingRect.Height } | Measure-Object -Maximum).Maximum
        $absoluteTop = $windowRect.Y + $top
        $isSearchOccurrence = $absoluteTop -ge ($searchRect.Y - 8) -and $absoluteTop -le ($searchRect.Y + $searchRect.Height + 8)
        $matches += [ordered]@{
            text = $line.Text
            imageRect = [ordered]@{ left = $left; top = $top; width = $right - $left; height = $bottom - $top }
            screenRect = [ordered]@{ left = $windowRect.X + $left; top = $absoluteTop; width = $right - $left; height = $bottom - $top }
            classification = if ($isSearchOccurrence) { "search-field" } else { "visual-result-candidate" }
        }
    }
    $resultCandidates = @($matches | Where-Object { $_.classification -eq "visual-result-candidate" })
    # UI Automation is authoritative for the exact search-field value. OCR only
    # needs to identify one distinct visual result; recognizing the tiny search
    # field text a second time is optional and varies with DPI/language rendering.
    $searchOcrOccurrences = @($matches | Where-Object { $_.classification -eq "search-field" })
    $safe = $resultCandidates.Count -eq 1 -and $searchOcrOccurrences.Count -le 1
    $dryRunTarget = $null
    if ($safe) {
        $targetRect = $resultCandidates[0].screenRect
        $dryRunTarget = [ordered]@{
            x = [math]::Round($targetRect.left + ($targetRect.width / 2.0))
            y = [math]::Round($targetRect.top + ($targetRect.height / 2.0))
            coordinateSpace = "virtual-screen-physical-pixels"
            basis = "center-of-unique-exact-result-text"
        }
    }
    $pointerBefore = $null
    $pointerAfter = $null
    $pointerMoved = $false
    if ($MovePointer) {
        if (-not $safe) { throw "Pointer movement refused because the hybrid result gate is not safe." }
        $imageFile = Get-Item -LiteralPath $ImagePath
        $imageAgeSeconds = ((Get-Date) - $imageFile.LastWriteTime).TotalSeconds
        if ($imageAgeSeconds -lt 0 -or $imageAgeSeconds -gt $MaxImageAgeSeconds) {
            throw "Pointer movement refused because the capture is $([math]::Round($imageAgeSeconds, 2)) seconds old."
        }
        $targetX = [int]$dryRunTarget.x
        $targetY = [int]$dryRunTarget.y
        if ($targetX -lt $windowRect.X -or $targetX -ge ($windowRect.X + $windowRect.Width) -or
            $targetY -lt $windowRect.Y -or $targetY -ge ($windowRect.Y + $windowRect.Height)) {
            throw "Pointer movement refused because the target is outside the current Premiere window."
        }
        Ensure-CursorApi
        $before = New-Object FxPalette.NativeCursor+Point
        if (-not [FxPalette.NativeCursor]::GetCursorPos([ref]$before)) { throw "Could not read the current cursor position." }
        $pointerBefore = [ordered]@{ x = $before.X; y = $before.Y }
        if (-not [FxPalette.NativeCursor]::SetCursorPos($targetX, $targetY)) { throw "SetCursorPos failed." }
        $after = New-Object FxPalette.NativeCursor+Point
        if (-not [FxPalette.NativeCursor]::GetCursorPos([ref]$after)) { throw "Could not verify the cursor position." }
        $pointerAfter = [ordered]@{ x = $after.X; y = $after.Y }
        $pointerMoved = $after.X -eq $targetX -and $after.Y -eq $targetY
        if (-not $pointerMoved) { throw "Cursor position did not match the validated target after movement." }
    }
    [ordered]@{
        ok = $true
        schemaVersion = 1
        actionType = "nativePresetAssist.inspectEffectsResult"
        data = [ordered]@{
            expectedQuery = $ExpectedQuery
            semanticQueryMatch = $true
            semanticSearchRect = Rect-Object $searchRect
            ocrLanguage = $engine.RecognizerLanguage.LanguageTag
            exactOcrOccurrenceCount = $matches.Count
            searchFieldOcrOccurrenceCount = $searchOcrOccurrences.Count
            visualResultCandidateCount = $resultCandidates.Count
            matches = $matches
            safeToDryRunTarget = $safe
            safetyReason = if ($safe) { "semantic-exact-query-plus-one-distinct-visual-result" } else { "ambiguous-or-missing-visual-result" }
            dryRunTarget = $dryRunTarget
            pointerMoveRequested = [bool]$MovePointer
            pointerBefore = $pointerBefore
            pointerAfter = $pointerAfter
            pointerMoved = $pointerMoved
            clickSynthesized = $false
            dragSynthesized = $false
            inputSynthesized = $pointerMoved
        }
    } | ConvertTo-Json -Depth 8
    exit $(if ($safe) { 0 } else { 2 })
} catch {
    [ordered]@{
        ok = $false
        schemaVersion = 1
        actionType = "nativePresetAssist.inspectEffectsResult"
        error = [ordered]@{ code = "RESULT_VALIDATION_FAILED"; message = $_.Exception.Message }
        data = [ordered]@{ inputSynthesized = $false }
    } | ConvertTo-Json -Depth 6
    exit 1
}
