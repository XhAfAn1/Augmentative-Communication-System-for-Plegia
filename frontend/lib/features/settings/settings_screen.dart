import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../../core/theme/app_colors.dart';
import '../../core/utils/tts_service.dart';
import '../keyboard/gaze_keyboard.dart' show flutterSpacebarEnabled, flutterDwellEnabled;

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  bool _gazeEnabled = true;
  double _dwellDuration = 1.0;
  bool _eegEnabled = false;

  // TTS Settings
  final TtsService _ttsService = TtsService();
  String _selectedEngine = 'edge_tts';
  String _selectedGender = 'female';
  String _selectedVoice = 'en-US-JennyNeural';
  double _ttsRate = 1.0;
  double _ttsPitch = 1.0;
  double _ttsTemperature = 0.3;
  bool _isTestingVoice = false;

  static const Map<String, Map<String, List<Map<String, String>>>> _ttsVoiceCatalog = {
    'edge_tts': {
      'female': [
        {'id': 'en-US-JennyNeural', 'name': 'Jenny (US Female — Conversational Flagship)'},
        {'id': 'en-US-AriaNeural', 'name': 'Aria (US Female — Expressive)'},
        {'id': 'en-GB-SoniaNeural', 'name': 'Sonia (UK Female — British)'},
      ],
      'male': [
        {'id': 'en-US-GuyNeural', 'name': 'Guy (US Male — Conversational Flagship)'},
        {'id': 'en-US-ChristopherNeural', 'name': 'Christopher (US Male — Professional)'},
        {'id': 'en-GB-RyanNeural', 'name': 'Ryan (UK Male — British)'},
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

  @override
  void initState() {
    super.initState();
    _loadGpuSettings();
    _loadTtsSettings();
  }

  Future<void> _loadTtsSettings() async {
    await _ttsService.init();
    if (mounted) {
      setState(() {
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

  Future<void> _toggleGpu(bool value) async {
    setState(() {
      _gpuEnabled = value;
      _isLoadingGpu = true;
      _gpuSubtitle = value ? 'Switching to GPU mode...' : 'Switching to CPU mode...';
    });

    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool('gpu_acceleration', value);
    } catch (_) {}

    try {
      final res = await http
          .post(
            Uri.parse('http://127.0.0.1:8000/api/settings/gpu'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'enabled': value}),
          )
          .timeout(const Duration(seconds: 15));

      if (res.statusCode == 200) {
        final data = jsonDecode(res.body);
        if (mounted) {
          setState(() {
            _gpuEnabled = data['gpu_enabled'] ?? value;
            final mode = data['active_mode'] ?? (value ? 'GPU Enabled' : 'CPU Mode');
            final device = data['device_name'] ?? 'NVIDIA GPU';
            _gpuSubtitle = '$mode ($device)';
            _isLoadingGpu = false;
          });
        }
        return;
      }
    } catch (e) {
      debugPrint('Failed to toggle GPU: $e');
    }

    if (mounted) {
      setState(() {
        _isLoadingGpu = false;
        _gpuSubtitle = value
            ? 'GPU Mode saved (Applied on backend)'
            : 'CPU Mode active';
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
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          const _SectionHeader('Gaze & Dwell Interaction'),
          _SettingCard(children: [
            _SwitchTile(
              title: 'Eye Tracking (Gaze Control)',
              subtitle: 'Enable camera-based gaze input',
              value: _gazeEnabled,
              onChanged: (v) => setState(() => _gazeEnabled = v),
              icon: Icons.remove_red_eye_outlined,
              iconColor: AppColors.primaryBlue,
            ),
            const Divider(),
            _SwitchTile(
              title: 'Spacebar Select',
              subtitle: 'Press spacebar to instantly select hovered item',
              value: flutterSpacebarEnabled.value,
              onChanged: (v) {
                setState(() {
                  flutterSpacebarEnabled.value = v;
                });
              },
              icon: Icons.space_bar,
              iconColor: const Color(0xFF22c55e),
            ),
            const Divider(),
            _SwitchTile(
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
                onChanged: (v) => setState(() => _dwellDuration = v),
              ),
            ),
          ]),
          const _SectionHeader('EEG / Brain Signal Input'),
          _SettingCard(children: [
            _SwitchTile(
              title: 'EEG Sensor Input',
              subtitle: 'Enable brain-computer interface signal',
              value: _eegEnabled,
              onChanged: (v) => setState(() => _eegEnabled = v),
              icon: Icons.psychology_outlined,
              iconColor: AppColors.accentCyan,
            ),
          ]),
          const _SectionHeader('AI & Hardware Acceleration'),
          _SettingCard(children: [
            _SwitchTile(
              title: 'GPU Acceleration (NVIDIA CUDA)',
              subtitle: _gpuSubtitle,
              value: _gpuEnabled,
              onChanged: _isLoadingGpu ? (_) {} : (v) => _toggleGpu(v),
              icon: Icons.memory,
              iconColor: const Color(0xFF10b981),
            ),
          ]),
          const _SectionHeader('Text-to-Speech (TTS) Configuration'),
          _SettingCard(children: [
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

            // 4. Speech Rate Slider
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
            const Divider(),

            // 5. Pitch Slider
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

            // 6. Voice Expressiveness (Temperature) — Pocket TTS specific
            if (_selectedEngine == 'pocket_tts') ...[
              const Divider(),
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
            ],
            const Divider(),

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
          const _SectionHeader('About AAC System'),
          _SettingCard(children: [
            ListTile(
              leading: const Icon(Icons.info_outline, color: AppColors.primaryBlue),
              title: Text('Version 1.0.0 (Phase 1 — UI Prototype)',
                  style: GoogleFonts.inter(fontWeight: FontWeight.w600)),
              subtitle: Text(
                'Intelligent Assistive Communication System\nfor individuals with severe motor disabilities.',
                style: GoogleFonts.inter(fontSize: 12),
              ),
            ),
          ]),
        ],
      ),
    );
  }
}

class _SectionHeader extends StatelessWidget {
  final String title;
  const _SectionHeader(this.title);

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 20, bottom: 8, left: 4),
      child: Text(title,
          style: GoogleFonts.inter(
              fontWeight: FontWeight.w700,
              fontSize: 13,
              color: AppColors.primaryBlue,
              letterSpacing: 0.5)),
    );
  }
}

class _SettingCard extends StatelessWidget {
  final List<Widget> children;
  const _SettingCard({required this.children});

  @override
  Widget build(BuildContext context) {
    return Card(
      elevation: 0,
      color: Colors.white,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: const BorderSide(color: AppColors.panelBorder),
      ),
      child: Column(children: children),
    );
  }
}

class _SwitchTile extends StatelessWidget {
  final String title, subtitle;
  final bool value;
  final void Function(bool) onChanged;
  final IconData icon;
  final Color iconColor;
  const _SwitchTile({
    required this.title,
    required this.subtitle,
    required this.value,
    required this.onChanged,
    required this.icon,
    required this.iconColor,
  });

  @override
  Widget build(BuildContext context) {
    return SwitchListTile(
      secondary: Icon(icon, color: iconColor),
      title: Text(title, style: GoogleFonts.inter(fontWeight: FontWeight.w600)),
      subtitle: Text(subtitle, style: GoogleFonts.inter(fontSize: 12)),
      value: value,
      activeColor: iconColor,
      onChanged: onChanged,
    );
  }
}
