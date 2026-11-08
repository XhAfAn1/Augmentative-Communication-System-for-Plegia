import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

/// TTS Service for SynapTalk AAC.
/// Supports Edge-TTS (Cloud Neural), Pocket TTS (On-Device 100M ALM), and Windows SAPI fallback.
class TtsService {
  // Singleton pattern for consistent configuration across screens
  static final TtsService _instance = TtsService._internal();
  factory TtsService() => _instance;
  TtsService._internal();

  bool _isSpeaking = false;
  Process? _fallbackProcess;

  String _engine = 'edge_tts'; // 'edge_tts', 'pocket_tts', 'sapi'
  String _voice = 'en-US-JennyNeural';
  String _gender = 'female';
  double _rate = 1.0;
  double _pitch = 1.0;
  double _temperature = 0.3;

  bool get isSpeaking => _isSpeaking;
  String get engine => _engine;
  String get voice => _voice;
  String get gender => _gender;
  double get rate => _rate;
  double get pitch => _pitch;
  double get temperature => _temperature;

  Future<void> init() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      _engine = prefs.getString('tts_engine') ?? 'edge_tts';
      _voice = prefs.getString('tts_voice') ?? 'en-US-JennyNeural';
      _gender = prefs.getString('tts_gender') ?? 'female';
      _rate = prefs.getDouble('tts_rate') ?? 1.0;
      _pitch = prefs.getDouble('tts_pitch') ?? 1.0;
      _temperature = prefs.getDouble('tts_temperature') ?? 0.3;

      // If pocket_tts is saved, warm it up in background on startup
      if (_engine == 'pocket_tts') {
        notifyEngineSelected('pocket_tts');
      }
    } catch (_) {}
  }

  Future<void> notifyEngineSelected(String engine) async {
    try {
      await http
          .post(
            Uri.parse('http://127.0.0.1:8000/api/tts/select_engine'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'engine': engine}),
          )
          .timeout(const Duration(seconds: 3));
      debugPrint('[TtsService] Notified backend of engine selection: $engine');
    } catch (e) {
      debugPrint('[TtsService] Could not notify engine selection: $e');
    }
  }

  Future<void> updateConfig({
    String? engine,
    String? voice,
    String? gender,
    double? rate,
    double? pitch,
    double? temperature,
  }) async {
    final oldEngine = _engine;
    if (engine != null) _engine = engine;
    if (voice != null) _voice = voice;
    if (gender != null) _gender = gender;
    if (rate != null) _rate = rate;
    if (pitch != null) _pitch = pitch;
    if (temperature != null) _temperature = temperature;

    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('tts_engine', _engine);
      await prefs.setString('tts_voice', _voice);
      await prefs.setString('tts_gender', _gender);
      await prefs.setDouble('tts_rate', _rate);
      await prefs.setDouble('tts_pitch', _pitch);
      await prefs.setDouble('tts_temperature', _temperature);
    } catch (_) {}

    // Preload Pocket TTS or unload if changed
    if (engine != null && engine != oldEngine) {
      notifyEngineSelected(engine);
    }
  }

  Future<void> speak(
    String text, {
    String? engine,
    String? voice,
    double? speed,
    double? pitch,
    double? temperature,
  }) async {
    final useText = text.trim();
    if (useText.isEmpty) return;

    // Refresh preferences to ensure latest voice/engine are active
    await init();

    await stop();
    _isSpeaking = true;

    final targetEngine = engine ?? _engine;
    final targetVoice = voice ?? _voice;
    final targetSpeed = speed ?? _rate;
    final targetPitch = pitch ?? _pitch;
    final targetTemperature = temperature ?? _temperature;

    debugPrint('[TtsService] Speaking with engine: $targetEngine, voice: $targetVoice');

    // 1. If Windows Native SAPI is explicitly chosen:
    if (targetEngine == 'sapi') {
      await _speakSapi(useText);
      _isSpeaking = false;
      return;
    }

    // 2. Call FastAPI backend TTS endpoint (Edge-TTS or Pocket TTS)
    try {
      final res = await http
          .post(
            Uri.parse('http://127.0.0.1:8000/api/tts/speak'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({
              'text': useText,
              'engine': targetEngine,
              'voice': targetVoice,
              'speed': targetSpeed,
              'pitch': targetPitch,
              'temperature': targetTemperature,
              'play_now': true,
            }),
          )
          .timeout(const Duration(seconds: 12));

      if (res.statusCode == 200) {
        debugPrint('[TtsService] Successfully synthesized and played via backend: ${res.body}');
        _isSpeaking = false;
        return;
      } else {
        debugPrint('[TtsService] Backend returned status ${res.statusCode}: ${res.body}, falling back to SAPI');
        await _speakSapi(useText);
      }
    } catch (e) {
      debugPrint('[TtsService] Backend error: $e, falling back to SAPI');
      await _speakSapi(useText);
    } finally {
      _isSpeaking = false;
    }
  }

  Future<void> _speakSapi(String text) async {
    final safe = text.replaceAll("'", "''");
    try {
      _fallbackProcess = await Process.start('powershell', [
        '-NoProfile',
        '-WindowStyle',
        'Hidden',
        '-Command',
        "Add-Type -AssemblyName System.Speech; "
            r"$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "\$s.Speak('$safe');",
      ]);
      await _fallbackProcess!.exitCode;
    } catch (_) {
      // Fail silently
    } finally {
      _fallbackProcess = null;
    }
  }

  Future<void> stop() async {
    _fallbackProcess?.kill();
    _fallbackProcess = null;

    // Notify backend to stop any playing audio
    try {
      http
          .post(Uri.parse('http://127.0.0.1:8000/api/tts/stop'))
          .timeout(const Duration(milliseconds: 500));
    } catch (_) {}

    _isSpeaking = false;
  }
}
