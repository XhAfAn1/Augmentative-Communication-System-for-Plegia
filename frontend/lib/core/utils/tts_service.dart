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

  static const _api = 'http://127.0.0.1:8000/api/tts';

  bool _isSpeaking = false;
  Process? _fallbackProcess;
  bool _loaded = false; // settings read from SharedPreferences (once; updateConfig keeps them current after that)
  int _seq = 0; // every speak / stop gets a new number: an older speak stops waiting for its sound

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

  /// Which settings an engine really uses (Settings hides the others).
  static bool usesRate(String engine) => engine == 'edge_tts' || engine == 'sapi';
  static bool usesPitch(String engine) => engine == 'edge_tts';

  /// Reads the saved settings; on app start also warms up Pocket TTS when it is the chosen engine.
  Future<void> init() async {
    await _loadPrefs();
    if (_engine == 'pocket_tts') notifyEngineSelected('pocket_tts');
  }

  Future<void> _loadPrefs() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      _engine = prefs.getString('tts_engine') ?? 'edge_tts';
      _voice = prefs.getString('tts_voice') ?? 'en-US-JennyNeural';
      _gender = prefs.getString('tts_gender') ?? 'female';
      _rate = prefs.getDouble('tts_rate') ?? 1.0;
      _pitch = prefs.getDouble('tts_pitch') ?? 1.0;
      _temperature = prefs.getDouble('tts_temperature') ?? 0.3;
      _loaded = true;
    } catch (_) {}
  }

  /// The backend's voice list (GET /api/tts/voices), so the app and the backend never disagree.
  /// Returns null when the backend is not reachable (Settings then uses its built-in list).
  Future<Map<String, Map<String, List<Map<String, String>>>>?> fetchVoiceCatalog() async {
    try {
      final r = await http.get(Uri.parse('$_api/voices')).timeout(const Duration(seconds: 3));
      if (r.statusCode != 200) return null;
      final out = <String, Map<String, List<Map<String, String>>>>{};
      for (final e in (jsonDecode(r.body)['engines'] as List)) {
        final byGender = <String, List<Map<String, String>>>{'female': [], 'male': []};
        for (final v in (e['voices'] as List)) {
          final entry = {'id': v['id'].toString(), 'name': v['name'].toString()};
          final g = v['gender']?.toString();
          if (e['id'] == 'sapi') {
            byGender['female']!.add(entry); // the one Windows voice is offered for both
            byGender['male']!.add(entry);
          } else if (byGender.containsKey(g)) {
            byGender[g]!.add(entry);
          }
        }
        out[e['id'].toString()] = byGender;
      }
      return out.isEmpty ? null : out;
    } catch (_) {
      return null;
    }
  }

  Future<void> notifyEngineSelected(String engine) async {
    try {
      await http
          .post(
            Uri.parse('$_api/select_engine'),
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
    _loaded = true;

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

  /// Speaks [text]; the returned future ends when the speech has FINISHED (or was stopped / replaced).
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
    if (!_loaded) await _loadPrefs();

    await stop();
    final seq = ++_seq;
    _isSpeaking = true;

    final targetEngine = engine ?? _engine;
    final targetVoice = voice ?? _voice;
    final targetSpeed = speed ?? _rate;
    final targetPitch = pitch ?? _pitch;
    final targetTemperature = temperature ?? _temperature;

    debugPrint('[TtsService] Speaking with engine: $targetEngine, voice: $targetVoice');

    try {
      // 1. If Windows Native SAPI is explicitly chosen:
      if (targetEngine == 'sapi') {
        await _speakSapi(useText, targetSpeed);
        return;
      }

      // 2. Call FastAPI backend TTS endpoint (Edge-TTS or Pocket TTS)
      try {
        final res = await http
            .post(
              Uri.parse('$_api/speak'),
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
          debugPrint('[TtsService] Synthesized via backend: ${res.body}');
          await _waitUntilQuiet(seq); // the backend only STARTS the sound
        } else {
          debugPrint('[TtsService] Backend returned status ${res.statusCode}: ${res.body}, falling back to SAPI');
          await _speakSapi(useText, targetSpeed);
        }
      } catch (e) {
        debugPrint('[TtsService] Backend error: $e, falling back to SAPI');
        await _stopBackend(); // a late backend result must not be spoken as well (double speech)
        if (seq == _seq) await _speakSapi(useText, targetSpeed);
      }
    } finally {
      if (seq == _seq) _isSpeaking = false;
    }
  }

  /// Waits while the backend still plays this sentence (GET /api/tts/status). Ends early when another speak or
  /// a stop replaces it, when the backend cannot be asked, or after 2 minutes at most.
  Future<void> _waitUntilQuiet(int seq) async {
    final until = DateTime.now().add(const Duration(minutes: 2));
    while (seq == _seq && DateTime.now().isBefore(until)) {
      await Future.delayed(const Duration(milliseconds: 250));
      if (seq != _seq) return;
      try {
        final r = await http.get(Uri.parse('$_api/status')).timeout(const Duration(seconds: 2));
        if (r.statusCode != 200 || jsonDecode(r.body)['playing'] != true) return;
      } catch (_) {
        return;
      }
    }
  }

  /// App speech rate 0.5..1.5 -> Windows SAPI Rate -5..+5 (0 = normal). Same mapping as the backend.
  static int sapiRate(double speed) => ((speed - 1.0) * 10).round().clamp(-10, 10);

  /// The text goes to PowerShell in an environment variable, never inside the command: PowerShell also treats
  /// curly quotes (’ ‘) as quote marks, so "I don’t know" used to break the command (silence).
  static const _sapiCommand = r"Add-Type -AssemblyName System.Speech; "
      r"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
      r"$s.Rate = [int]$env:AAC_TTS_RATE; "
      r"$s.Speak($env:AAC_TTS_TEXT);";

  Future<void> _speakSapi(String text, double speed) async {
    Process? p;
    try {
      p = await Process.start(
        'powershell',
        ['-NoProfile', '-WindowStyle', 'Hidden', '-Command', _sapiCommand],
        environment: {'AAC_TTS_TEXT': text, 'AAC_TTS_RATE': '${sapiRate(speed)}'},
      );
      _fallbackProcess = p;
      await p.exitCode;
    } catch (_) {
      // Fail silently
    } finally {
      // only forget OUR process: a newer sentence may already have started its own (Stop must still reach it)
      if (identical(_fallbackProcess, p)) _fallbackProcess = null;
    }
  }

  Future<void> stop() async {
    _seq++;
    _fallbackProcess?.kill();
    _fallbackProcess = null;

    await _stopBackend();
    _isSpeaking = false;
  }

  /// Stops the backend's audio and cancels what it is still synthesizing. Awaited, so it always reaches the
  /// backend BEFORE the next speak request (otherwise it could cancel the new sentence).
  Future<void> _stopBackend() async {
    try {
      await http.post(Uri.parse('$_api/stop')).timeout(const Duration(milliseconds: 500));
    } catch (_) {}
  }
}
