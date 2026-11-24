import 'dart:async' show TimeoutException;
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../../core/theme/app_colors.dart';
import '../../core/utils/tts_service.dart';
import '../../core/services/tracker_client.dart';
import '../../core/widgets/dwell_wrapper.dart' show dwellDurationMs;
import '../../core/widgets/ui_kit.dart';
import '../tracking/emg_card.dart';
import '../keyboard/gaze_keyboard.dart' show flutterSpacebarEnabled, flutterDwellEnabled, selectionSoundEnabled, selectionFeedback;

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  double _dwellDuration = dwellDurationMs.value / 1000.0;

  // TTS Settings
  final TtsService _ttsService = TtsService();
  String _selectedEngine = 'edge_tts';
  String _selectedGender = 'female';
  String _selectedVoice = 'en-US-JennyNeural';
  double _ttsRate = 1.0;
  double _ttsPitch = 1.0;
  double _ttsTemperature = 0.3;
  bool _isTestingVoice = false;

  // voices: loaded from the backend (GET /api/tts/voices) in _loadTtsSettings; this built-in copy is only used
  // while the backend is offline
  Map<String, Map<String, List<Map<String, String>>>> _ttsVoiceCatalog = _defaultVoiceCatalog;
  static const Map<String, Map<String, List<Map<String, String>>>> _defaultVoiceCatalog = {
    'edge_tts': {
      'female': [
        {'id': 'en-US-JennyNeural', 'name': 'Jenny (US Female - Conversational Flagship)'},
        {'id': 'en-US-AriaNeural', 'name': 'Aria (US Female - Expressive)'},
        {'id': 'en-GB-SoniaNeural', 'name': 'Sonia (UK Female - British)'},
      ],
      'male': [
        {'id': 'en-US-GuyNeural', 'name': 'Guy (US Male - Conversational Flagship)'},
        {'id': 'en-US-ChristopherNeural', 'name': 'Christopher (US Male - Professional)'},
        {'id': 'en-GB-RyanNeural', 'name': 'Ryan (UK Male - British)'},
      ],
    },
    'pocket_tts': {
      'female': [
        {'id': 'anna', 'name': 'Anna (Gentle Female)'},
        {'id': 'cosette', 'name': 'Cosette (Melodic Female)'},
        {'id': 'jane', 'name': 'Jane (Grounded Female)'},
      ],
      'male': [
        {'id': 'jean', 'name': 'Jean (Natural Male)'},
        {'id': 'paul', 'name': 'Paul (Warm Male)'},
        {'id': 'george', 'name': 'George (Deep Male)'},
        {'id': 'michael', 'name': 'Michael (Clean Male)'},
      ],
    },
    'sapi': {
      'female': [
        {'id': 'default', 'name': 'Windows Default Voice'},
      ],
      'male': [
        {'id': 'default', 'name': 'Windows Default Voice'},
      ],
    },
  };

  // GPU & AI Hardware Acceleration
  bool _gpuEnabled = true;
  bool _isLoadingGpu = false;
  String _gpuSubtitle = 'Detecting GPU and AI status...';

  // Prediction profile (backend): each profile learns only from its own spoken sentences (Test vs Patient)
  String? _profile;
  List<String> _profiles = const [];
  int _profilePhrases = 0;

  Future<void> _loadProfile([String? switchTo]) async {
    try {
      final uri = Uri.parse('http://127.0.0.1:8000/api/profile');
      final r = switchTo == null
          ? await http.get(uri).timeout(const Duration(seconds: 3))
          : await http
              .post(uri, headers: {'Content-Type': 'application/json'}, body: jsonEncode({'name': switchTo}))
              .timeout(const Duration(seconds: 10));
      if (r.statusCode != 200) return;
      final d = jsonDecode(r.body) as Map<String, dynamic>;
      if (!mounted) return;
      setState(() {
        _profile = d['active']?.toString();
        _profiles = ((d['profiles'] as List?) ?? const []).map((e) => e.toString()).toList();
        _profilePhrases = (d['phrases'] as num?)?.toInt() ?? 0;
      });
    } catch (_) {}
  }

  @override
  void initState() {
    super.initState();
    _loadProfile();
    _loadGpuSettings();
    _loadTtsSettings();
  }

  Future<void> _loadTtsSettings() async {
    await _ttsService.init();
    final catalog = await _ttsService.fetchVoiceCatalog();
    if (mounted) {
      setState(() {
        if (catalog != null) _ttsVoiceCatalog = catalog;
        _selectedEngine = _ttsService.engine;
        _selectedGender = _ttsService.gender;
        _selectedVoice = _ttsService.voice;
        _ttsRate = _ttsService.rate;
        _ttsPitch = _ttsService.pitch;
        _ttsTemperature = _ttsService.temperature;
        _ensureValidVoice();
      });
      // Ensure synchronized state if valid voice changed
      if (_selectedVoice != _ttsService.voice) {
        _saveTtsSettings();
      }
    }
  }

  void _ensureValidVoice() {
    final available = _ttsVoiceCatalog[_selectedEngine]?[_selectedGender] ?? [];
    if (!available.any((v) => v['id'] == _selectedVoice)) {
      if (available.isNotEmpty) {
        _selectedVoice = available.first['id']!;
      }
    }
  }

  Future<void> _saveTtsSettings() async {
    await _ttsService.updateConfig(
      engine: _selectedEngine,
      gender: _selectedGender,
      voice: _selectedVoice,
      rate: _ttsRate,
      pitch: _ttsPitch,
      temperature: _ttsTemperature,
    );
  }

  Future<void> _testVoiceSample() async {
    if (_isTestingVoice) return;
    setState(() => _isTestingVoice = true);
    await _ttsService.speak(
      'Hello! This is a test of my assistive speech voice.',
      engine: _selectedEngine,
      voice: _selectedVoice,
      speed: _ttsRate,
      pitch: _ttsPitch,
      temperature: _ttsTemperature,
    );
    if (mounted) setState(() => _isTestingVoice = false);
  }

  Future<void> _loadGpuSettings() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final cachedGpu = prefs.getBool('gpu_acceleration') ?? true;
      if (mounted) {
        setState(() => _gpuEnabled = cachedGpu);
      }
    } catch (_) {}

    try {
      final res = await http
          .get(Uri.parse('http://127.0.0.1:8000/api/settings/gpu'))
          .timeout(const Duration(seconds: 2));
      if (res.statusCode == 200) {
        final data = jsonDecode(res.body);
        if (mounted) {
          setState(() {
            _gpuEnabled = data['gpu_enabled'] ?? _gpuEnabled;
            final mode = data['active_mode'] ?? (_gpuEnabled ? 'GPU Enabled' : 'CPU Mode');
            final device = data['device_name'] ?? 'NVIDIA GPU';
            _gpuSubtitle = '$mode ($device)';
          });
        }
      }
    } catch (_) {
      if (mounted) {
        setState(() {
          _gpuSubtitle = _gpuEnabled
              ? 'GPU Mode preferred (Backend offline)'
              : 'CPU Mode active';
        });
      }
    }
  }

  /// The backend saves the GPU choice itself (backend_settings.json) and reloads both models for it, which can
  /// take a while on the CPU. The app's own copy ('gpu_acceleration', only for showing the switch while the
  /// backend is offline) is saved only after the backend confirmed the change.
  Future<void> _toggleGpu(bool value) async {
    final before = _gpuEnabled;
    setState(() {
      _gpuEnabled = value;
      _isLoadingGpu = true;
      _gpuSubtitle = value ? 'Switching to GPU mode... (reloading the AI models)' : 'Switching to CPU mode... (reloading the AI models)';
    });

    String failure;
    try {
      final res = await http
          .post(
            Uri.parse('http://127.0.0.1:8000/api/settings/gpu'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'enabled': value}),
          )
          .timeout(const Duration(minutes: 3));

      if (res.statusCode == 200) {
        final data = jsonDecode(res.body);
        final bool applied = data['gpu_enabled'] ?? value;
        try {
          final prefs = await SharedPreferences.getInstance();
          await prefs.setBool('gpu_acceleration', applied);
        } catch (_) {}
        if (mounted) {
          setState(() {
            _gpuEnabled = applied;
            final mode = data['active_mode'] ?? (applied ? 'GPU Enabled' : 'CPU Mode');
            final device = data['device_name'] ?? 'NVIDIA GPU';
            _gpuSubtitle = '$mode ($device)';
            _isLoadingGpu = false;
          });
        }
        return;
      }
      failure = res.statusCode == 503
          ? 'AI backend is still starting · GPU setting not changed, try again in a moment'
          : 'AI backend error ${res.statusCode} · GPU setting not changed';
    } on TimeoutException {
      // the backend may still finish the switch: the real state is shown the next time Settings opens
      if (mounted) {
        setState(() {
          _isLoadingGpu = false;
          _gpuSubtitle = 'Still switching (the AI backend is reloading the models) · open Settings again to check';
        });
      }
      return;
    } catch (e) {
      debugPrint('Failed to toggle GPU: $e');
      failure = 'AI backend not reachable · GPU setting not changed';
    }

    if (mounted) {
      setState(() {
        _gpuEnabled = before; // nothing reached the backend: the switch shows what is really used
        _isLoadingGpu = false;
        _gpuSubtitle = failure;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.appBackground,
      appBar: AppBar(
        backgroundColor: AppColors.keyboardDark,
        foregroundColor: Colors.white,
        title: Text('Settings', style: GoogleFonts.inter(fontWeight: FontWeight.w700)),
        leading: IconButton(
          icon: const Icon(Icons.arrow_back_ios_new),
          onPressed: () => context.pop(),
        ),
      ),
      body: MenuPage(child: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          const SectionHeader('Gaze & Dwell Interaction'),
          SettingCard(children: [
            ListenableBuilder(
              listenable: TrackerClient.instance,
              builder: (context, _) {
                final st = TrackerClient.instance.status;
                return SwitchTile(
                  title: 'Eye Tracking (Gaze Control)',
                  subtitle: st == null
                      ? 'Eye tracker not running'
                      : st.mouseEnabled
                          ? 'The eye mouse moves the cursor (F5 toggles, F6 stops)'
                          : 'Eye mouse off · ${st.calibrated ? 'calibration grade ${st.gradeLetter}' : 'not calibrated'}',
                  value: st?.mouseEnabled ?? false,
                  onChanged: st == null ? null : (v) => TrackerClient.instance.setMouseEnabled(v),
                  icon: Icons.remove_red_eye_outlined,
                  iconColor: AppColors.primaryBlue,
                );
              },
            ),
            ListTile(
              leading: const Icon(Icons.center_focus_strong_outlined, color: AppColors.primaryBlue),
              title: Text('Eye Tracking Setup & Calibration', style: GoogleFonts.inter(fontWeight: FontWeight.w600)),
              subtitle: Text('Cameras, calibration, fine-tune, re-centre and cursor tuning',
                  style: GoogleFonts.inter(fontSize: 12)),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => context.push('/tracking'),
            ),
            const Divider(),
            ValueListenableBuilder<bool>(
              valueListenable: flutterSpacebarEnabled, // also changes from the Tracking screen's Spacebar Click
              builder: (context, on, _) => SwitchTile(
                title: 'Spacebar Select',
                subtitle: "Press spacebar to instantly select hovered item · same switch as the eye tracker's Spacebar Click",
                value: on,
                onChanged: (v) => TrackerClient.instance.setSpacebarClick(v),
                icon: Icons.space_bar,
                iconColor: const Color(0xFF22c55e),
              ),
            ),
            const Divider(),
            ValueListenableBuilder<bool>(
              valueListenable: selectionSoundEnabled,
              builder: (context, on, _) => SwitchTile(
                title: 'Selection Sound',
                subtitle: 'A short click on every selection (dwell, Spacebar or mouse)',
                value: on,
                onChanged: (v) {
                  selectionSoundEnabled.value = v;
                  selectionFeedback(); // let the user hear it when switching on
                },
                icon: Icons.volume_up_outlined,
                iconColor: const Color(0xFF22c55e),
              ),
            ),
            const Divider(),
            SwitchTile(
              title: 'Dwell Auto-Select',
              subtitle: 'Automatically select buttons by looking at them',
              value: flutterDwellEnabled.value,
              onChanged: (v) {
                setState(() {
                  flutterDwellEnabled.value = v;
                });
              },
              icon: Icons.mouse_outlined,
              iconColor: AppColors.accentCyan,
            ),
            const Divider(),
            ListTile(
              leading: const Icon(Icons.timer_outlined, color: AppColors.accentCyan),
              title: Text('Dwell Time: ${_dwellDuration.toStringAsFixed(1)}s',
                  style: GoogleFonts.inter(fontWeight: FontWeight.w600)),
              subtitle: Slider(
                value: _dwellDuration,
                min: 0.5,
                max: 4.0,
                divisions: 7,
                label: '${_dwellDuration.toStringAsFixed(1)}s',
                activeColor: AppColors.primaryBlue,
                onChanged: (v) {
                  setState(() => _dwellDuration = v);
                  dwellDurationMs.value = (v * 1000).round();
                },
                onChangeEnd: (v) async {
                  try {
                    final prefs = await SharedPreferences.getInstance();
                    await prefs.setInt('dwell_ms', (v * 1000).round());
                  } catch (_) {}
                },
              ),
            ),
          ]),
          const SectionHeader('EMG / Muscle Signal Input'),
          const EmgCard(), // switch + EMG View together (same card as Eye Tracking > Sensors)
          const SectionHeader('Prediction Profile'),
          SettingCard(children: [
            ListTile(
              leading: const Icon(Icons.person_outline, color: AppColors.primaryBlue),
              title: Text('Profile', style: GoogleFonts.inter(fontWeight: FontWeight.w600)),
              subtitle: Text(
                  _profile == null
                      ? 'AI backend not running'
                      : 'Predictions learn from and use only this profile ($_profilePhrases saved sentences). '
                          'Use Test for trying things out, Patient for real use.',
                  style: GoogleFonts.inter(fontSize: 12)),
              trailing: _profile == null
                  ? null
                  : DropdownButton<String>(
                      value: _profile,
                      items: [for (final p in _profiles) DropdownMenuItem(value: p, child: Text(p))],
                      onChanged: (v) {
                        if (v != null && v != _profile) _loadProfile(v);
                      },
                    ),
            ),
          ]),
          const SectionHeader('AI & Hardware Acceleration'),
          SettingCard(children: [
            SwitchTile(
              title: 'GPU Acceleration (NVIDIA CUDA)',
              subtitle: _gpuSubtitle,
              value: _gpuEnabled,
              onChanged: _isLoadingGpu ? (_) {} : (v) => _toggleGpu(v),
              icon: Icons.memory,
              iconColor: const Color(0xFF10b981),
            ),
          ]),
          const SectionHeader('Text-to-Speech (TTS) Configuration'),
          SettingCard(children: [
            // 1. Engine Dropdown
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
              child: Row(
                children: [
                  const Icon(Icons.record_voice_over_outlined, color: AppColors.primaryBlue),
                  const SizedBox(width: 16),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('TTS Engine',
                            style: GoogleFonts.inter(fontWeight: FontWeight.w600, fontSize: 14)),
                        Text('Select neural cloud or on-device edge model',
                            style: GoogleFonts.inter(fontSize: 12, color: Colors.grey[600])),
                      ],
                    ),
                  ),
                  DropdownButton<String>(
                    value: _selectedEngine,
                    underline: const SizedBox(),
                    items: const [
                      DropdownMenuItem(
                        value: 'edge_tts',
                        child: Text('Edge-TTS (Azure Cloud)'),
                      ),
                      DropdownMenuItem(
                        value: 'pocket_tts',
                        child: Text('Pocket TTS (On-Device 100M)'),
                      ),
                      DropdownMenuItem(
                        value: 'sapi',
                        child: Text('Windows Native (SAPI)'),
                      ),
                    ],
                    onChanged: (val) {
                      if (val != null) {
                        setState(() {
                          _selectedEngine = val;
                          _ensureValidVoice();
                        });
                        _saveTtsSettings();
                      }
                    },
                  ),
                ],
              ),
            ),
            const Divider(),

            // 2. Gender Selection
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: Row(
                children: [
                  const Icon(Icons.wc_outlined, color: AppColors.primaryBlue, size: 20),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text('Voice Persona Gender',
                        style: GoogleFonts.inter(fontWeight: FontWeight.w600, fontSize: 14)),
                  ),
                  Wrap(
                    spacing: 8,
                    children: [
                      ChoiceChip(
                        label: const Text('Female'),
                        selected: _selectedGender == 'female',
                        selectedColor: AppColors.primaryBlueLight.withValues(alpha: 0.2),
                        labelStyle: GoogleFonts.inter(
                          fontWeight: FontWeight.w600,
                          color: _selectedGender == 'female' ? AppColors.primaryBlue : Colors.black87,
                        ),
                        onSelected: (selected) {
                          if (selected) {
                            setState(() {
                              _selectedGender = 'female';
                              _ensureValidVoice();
                            });
                            _saveTtsSettings();
                          }
                        },
                      ),
                      ChoiceChip(
                        label: const Text('Male'),
                        selected: _selectedGender == 'male',
                        selectedColor: AppColors.primaryBlueLight.withValues(alpha: 0.2),
                        labelStyle: GoogleFonts.inter(
                          fontWeight: FontWeight.w600,
                          color: _selectedGender == 'male' ? AppColors.primaryBlue : Colors.black87,
                        ),
                        onSelected: (selected) {
                          if (selected) {
                            setState(() {
                              _selectedGender = 'male';
                              _ensureValidVoice();
                            });
                            _saveTtsSettings();
                          }
                        },
                      ),
                    ],
                  ),
                ],
              ),
            ),
            const Divider(),

            // 3. Voice Persona Dropdown
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
              child: Row(
                children: [
                  const Icon(Icons.person_pin_circle_outlined, color: AppColors.primaryBlue),
                  const SizedBox(width: 16),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('Voice Persona',
                            style: GoogleFonts.inter(fontWeight: FontWeight.w600, fontSize: 14)),
                        Text('Available voices for selected engine',
                            style: GoogleFonts.inter(fontSize: 12, color: Colors.grey[600])),
                      ],
                    ),
                  ),
                  DropdownButton<String>(
                    value: _selectedVoice,
                    underline: const SizedBox(),
                    items: (_ttsVoiceCatalog[_selectedEngine]?[_selectedGender] ?? []).map((voice) {
                      return DropdownMenuItem<String>(
                        value: voice['id'],
                        child: Text(
                          voice['name'] ?? '',
                          style: GoogleFonts.inter(fontSize: 13),
                        ),
                      );
                    }).toList(),
                    onChanged: (val) {
                      if (val != null) {
                        setState(() => _selectedVoice = val);
                        _saveTtsSettings();
                      }
                    },
                  ),
                ],
              ),
            ),
            const Divider(),

            // 4. Speech Rate Slider (Edge-TTS and Windows voice; Pocket TTS has no rate setting)
            if (TtsService.usesRate(_selectedEngine))
            ListTile(
              leading: const Icon(Icons.speed, color: AppColors.primaryBlue),
              title: Text('Speech Rate: ${_ttsRate.toStringAsFixed(1)}x',
                  style: GoogleFonts.inter(fontWeight: FontWeight.w600)),
              subtitle: Slider(
                value: _ttsRate,
                min: 0.5,
                max: 1.5,
                divisions: 10,
                activeColor: AppColors.primaryBlue,
                onChanged: (v) {
                  setState(() => _ttsRate = v);
                  _saveTtsSettings();
                },
              ),
            ),
            if (TtsService.usesRate(_selectedEngine)) const Divider(),

            // 5. Pitch Slider (Edge-TTS only)
            if (TtsService.usesPitch(_selectedEngine))
            ListTile(
              leading: const Icon(Icons.graphic_eq, color: AppColors.primaryBlue),
              title: Text('Pitch: ${_ttsPitch.toStringAsFixed(1)}',
                  style: GoogleFonts.inter(fontWeight: FontWeight.w600)),
              subtitle: Slider(
                value: _ttsPitch,
                min: 0.5,
                max: 1.5,
                divisions: 10,
                activeColor: AppColors.primaryBlue,
                onChanged: (v) {
                  setState(() => _ttsPitch = v);
                  _saveTtsSettings();
                },
              ),
            ),
            if (TtsService.usesPitch(_selectedEngine)) const Divider(),

            // 6. Voice Expressiveness (Temperature) — Pocket TTS specific
            if (_selectedEngine == 'pocket_tts') ...[
              ListTile(
                leading: const Icon(Icons.tune_rounded, color: AppColors.primaryBlue),
                title: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text('Expressiveness (Temp): ${_ttsTemperature.toStringAsFixed(2)}',
                        style: GoogleFonts.inter(fontWeight: FontWeight.w600)),
                    Text(
                      _ttsTemperature <= 0.2
                          ? 'Calm / Steady'
                          : (_ttsTemperature <= 0.45 ? 'Balanced (Default)' : 'Dynamic / Expressive'),
                      style: GoogleFonts.inter(
                        fontSize: 12,
                        color: AppColors.primaryBlue,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ],
                ),
                subtitle: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const SizedBox(height: 4),
                    Text(
                      'Controls Pocket-TTS flow temperature (0.1 = stable & predictable, 0.6 = animated prosody)',
                      style: GoogleFonts.inter(fontSize: 11, color: Colors.grey[600]),
                    ),
                    Slider(
                      value: _ttsTemperature,
                      min: 0.1,
                      max: 0.7,
                      divisions: 12,
                      activeColor: AppColors.primaryBlue,
                      onChanged: (v) {
                        setState(() => _ttsTemperature = v);
                        _saveTtsSettings();
                      },
                    ),
                  ],
                ),
              ),
              const Divider(),
            ],

            // 7. Test Voice Button
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
              child: SizedBox(
                width: double.infinity,
                child: ElevatedButton.icon(
                  onPressed: _isTestingVoice ? null : _testVoiceSample,
                  icon: _isTestingVoice
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                        )
                      : const Icon(Icons.volume_up_rounded),
                  label: Text(_isTestingVoice ? 'Speaking Sample...' : 'Test Voice Sample'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppColors.primaryBlue,
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(8),
                    ),
                  ),
                ),
              ),
            ),
          ]),
          const SectionHeader('About AAC System'),
          SettingCard(children: [
            ListTile(
              leading: const Icon(Icons.info_outline, color: AppColors.primaryBlue),
              title: Text('Version 1.2.0',
                  style: GoogleFonts.inter(fontWeight: FontWeight.w600)),
              subtitle: Text(
                'Intelligent Assistive Communication System\nfor individuals with severe motor disabilities.',
                style: GoogleFonts.inter(fontSize: 12),
              ),
            ),
            const Divider(),
            ListTile(
              leading: const Icon(Icons.dashboard_outlined, color: AppColors.primaryBlue),
              title: Text('System Status', style: GoogleFonts.inter(fontWeight: FontWeight.w600)),
              subtitle: Text('Eye tracker and AI backend', style: GoogleFonts.inter(fontSize: 12)),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => context.push('/dashboard'),
            ),
          ]),
        ],
      )),
    );
  }
}
