import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:google_fonts/google_fonts.dart';
import '../../core/theme/app_colors.dart';

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
          _SectionHeader('Gaze & Dwell Interaction'),
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
          _SectionHeader('EEG / Brain Signal Input'),
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
          _SectionHeader('Text-to-Speech (TTS)'),
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
          _SectionHeader('About SynapTalk'),
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
