import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../../core/theme/app_colors.dart';
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
  double _ttsRate = 1.0;
  double _ttsPitch = 1.0;

  // GPU & AI Hardware Acceleration
  bool _gpuEnabled = true;
  bool _isLoadingGpu = false;
  String _gpuSubtitle = 'Detecting GPU and AI status...';

  @override
  void initState() {
    super.initState();
    _loadGpuSettings();
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
          const _SectionHeader('Text-to-Speech (TTS)'),
          _SettingCard(children: [
            ListTile(
              leading: const Icon(Icons.speed, color: AppColors.primaryBlue),
              title: Text('Speech Rate: ${_ttsRate.toStringAsFixed(1)}',
                  style: GoogleFonts.inter(fontWeight: FontWeight.w600)),
              subtitle: Slider(
                value: _ttsRate,
                min: 0.1,
                max: 1.0,
                divisions: 9,
                activeColor: AppColors.primaryBlue,
                onChanged: (v) => setState(() => _ttsRate = v),
              ),
            ),
            const Divider(),
            ListTile(
              leading: const Icon(Icons.graphic_eq, color: AppColors.primaryBlue),
              title: Text('Pitch: ${_ttsPitch.toStringAsFixed(1)}',
                  style: GoogleFonts.inter(fontWeight: FontWeight.w600)),
              subtitle: Slider(
                value: _ttsPitch,
                min: 0.5,
                max: 2.0,
                divisions: 15,
                activeColor: AppColors.primaryBlue,
                onChanged: (v) => setState(() => _ttsPitch = v),
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
