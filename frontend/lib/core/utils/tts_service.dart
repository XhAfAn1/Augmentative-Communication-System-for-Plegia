import 'dart:io';

/// TTS Service — Windows-native implementation using PowerShell SAPI.
/// Phase 2+: replace with a proper package or on-device neural TTS model.
class TtsService {
  bool _isSpeaking = false;
  Process? _process;

  bool get isSpeaking => _isSpeaking;

  Future<void> init() async {
    // No init needed for PowerShell-based TTS
  }

  Future<void> speak(String text) async {
    await stop();
    _isSpeaking = true;
    // Escape single quotes and special chars for PowerShell
    final safe = text.replaceAll("'", "''");
    try {
      _process = await Process.start('powershell', [
        '-Command',
        "Add-Type -AssemblyName System.Speech; "
            r"$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "\$s.Speak('$safe');",
      ]);
      await _process!.exitCode;
    } catch (_) {
      // TTS not available — silently fail in Phase 1
    } finally {
      _isSpeaking = false;
    }
  }

  Future<void> stop() async {
    _process?.kill();
    _process = null;
    _isSpeaking = false;
  }
}
