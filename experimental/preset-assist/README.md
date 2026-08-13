# Native preset-assist experiment

This isolated Windows helper investigates native drag-and-drop for presets that official Premiere UXP cannot reproduce faithfully. It is not part of the official execution adapter and does not communicate with the stable Python + CEP application.

The first diagnostic is strictly read-only: it enumerates visible windows owned by the exact `Adobe Premiere Pro.exe` executable, requires a unique or foreground candidate, enables per-monitor DPI awareness, and can capture only that window. It never changes focus or synthesizes input. Window-title text is not trusted because current localized/project-bearing titles may say only `Adobe Premiere - ...`.

```powershell
python experimental/preset-assist/diagnose_premiere_window.py
python experimental/preset-assist/diagnose_premiere_window.py --output "$env:TEMP\fxpalette-premiere-window.png"
```

Future gates, in order, are panel-region calibration, OCR/result ambiguity detection, dry-run pointer targeting, user-confirmed drag acquisition, and post-drop UXP verification. Every gate must fail closed.

Premiere 26.3.2 exposes the Effects search Edit through Windows UI Automation, including its exact current text, but does not expose the visible filtered result row. The result validator must therefore be hybrid: semantic exact-query verification plus visual detection/OCR of the preset row and icon. A pixel-only or UIA-only decision is forbidden.

`inspect_effects_result.ps1` implements the first hybrid read-only gate with the native Windows OCR engine. It requires exactly one visible Premiere search Edit equal to the requested query, OCRs the saved Premiere-window capture, classifies the OCR occurrence overlapping the semantic Edit as the search field, and approves a future dry run only when exactly one other exact-text line remains. It does not yet classify the preset icon or move the pointer.

```powershell
powershell -NoProfile -File experimental/preset-assist/inspect_effects_result.ps1 `
  -ImagePath "$env:TEMP\fxpalette-premiere-search-result.png" `
  -ExpectedQuery "TESTE SUPREMO"
```

The alias bridge export remains research evidence only. It was rejected as a product workflow because importing generated copies would pollute and desynchronize the user's Presets catalog.
