# Native preset-assist experiment

This isolated Windows helper investigates native drag-and-drop for presets that official Premiere UXP cannot reproduce faithfully. It is not part of the official execution adapter and does not communicate with the stable Python + CEP application.

The first diagnostic is strictly read-only: it enumerates visible windows owned by the exact `Adobe Premiere Pro.exe` executable, requires a unique or foreground candidate, enables per-monitor DPI awareness, and can capture only that window. It never changes focus or synthesizes input. Window-title text is not trusted because current localized/project-bearing titles may say only `Adobe Premiere - ...`.

```powershell
python experimental/preset-assist/diagnose_premiere_window.py
python experimental/preset-assist/diagnose_premiere_window.py --output "$env:TEMP\fxpalette-premiere-window.png"
python experimental/preset-assist/diagnose_premiere_window.py --wait-seconds 30 --output "$env:TEMP\fxpalette-premiere-window.png"
```

`--wait-seconds` polls without focusing or controlling Premiere and captures as soon as a visible, executable-validated window becomes available. The accepted range is 0–60 seconds.

Future gates, in order, are panel-region calibration, OCR/result ambiguity detection, dry-run pointer targeting, user-confirmed drag acquisition, and post-drop UXP verification. Every gate must fail closed.

Premiere 26.3.2 exposes the Effects search Edit through Windows UI Automation, including its exact current text, but does not expose the visible filtered result row. The result validator must therefore be hybrid: semantic exact-query verification plus visual detection/OCR of the preset row and icon. A pixel-only or UIA-only decision is forbidden.

`inspect_effects_result.ps1` implements the first hybrid read-only gate with the native Windows OCR engine. It requires exactly one visible Premiere search Edit equal to the requested query, OCRs the saved Premiere-window capture, and approves a dry-run target only when exactly one distinct visual result line has the same exact text. OCR recognition of the small search-field text is optional because UI Automation already verifies that field authoritatively. A successful result reports the center of the unique exact result text in physical virtual-screen coordinates; it never moves the pointer or synthesizes input.

```powershell
powershell -NoProfile -File experimental/preset-assist/inspect_effects_result.ps1 `
  -ImagePath "$env:TEMP\fxpalette-premiere-search-result.png" `
  -ExpectedQuery "TESTE SUPREMO"
```

The next opt-in gate recaptures Premiere, repeats the hybrid validation, requires the PNG to be no more than five seconds old, verifies that the target remains inside the current Premiere window, and moves only the pointer to the validated text center. It never clicks, presses, releases or drags:

```powershell
powershell -NoProfile -File experimental/preset-assist/capture_and_point.ps1 `
  -ExpectedQuery "TESTE SUPREMO"
```

The alias bridge export remains research evidence only. It was rejected as a product workflow because importing generated copies would pollute and desynchronize the user's Presets catalog.
