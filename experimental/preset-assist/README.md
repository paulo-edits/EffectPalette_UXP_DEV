# Native preset-assist experiment

This isolated Windows helper investigates native drag-and-drop for presets that official Premiere UXP cannot reproduce faithfully. It is not part of the official execution adapter and does not communicate with the stable Python + CEP application.

The first diagnostic is strictly read-only: it enumerates visible Adobe Premiere Pro windows, requires a unique or foreground candidate, enables per-monitor DPI awareness, and can capture only that window. It never changes focus or synthesizes input.

```powershell
python experimental/preset-assist/diagnose_premiere_window.py
python experimental/preset-assist/diagnose_premiere_window.py --output "$env:TEMP\fxpalette-premiere-window.png"
```

Future gates, in order, are panel-region calibration, OCR/result ambiguity detection, dry-run pointer targeting, user-confirmed drag acquisition, and post-drop UXP verification. Every gate must fail closed.

The alias bridge export remains research evidence only. It was rejected as a product workflow because importing generated copies would pollute and desynchronize the user's Presets catalog.
