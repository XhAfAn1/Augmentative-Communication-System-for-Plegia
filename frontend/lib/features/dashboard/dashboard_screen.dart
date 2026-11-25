import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:google_fonts/google_fonts.dart';
import '../../core/services/system_processes.dart';
import '../../core/services/tracker_client.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/ui_kit.dart';

class DashboardScreen extends StatelessWidget {
  const DashboardScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.appBackground,
      appBar: AppBar(
        backgroundColor: AppColors.keyboardDark,
        foregroundColor: Colors.white,
        title: Text('System Dashboard', style: GoogleFonts.inter(fontWeight: FontWeight.w700)),
        leading: IconButton(
          icon: const Icon(Icons.arrow_back_ios_new),
          onPressed: () => context.pop(),
        ),
      ),
      body: MenuPage(
        maxWidth: 1200,
        child: ListenableBuilder(
          listenable: Listenable.merge([SystemProcesses.instance, TrackerClient.instance]),
          builder: (context, _) => ListView(
            padding: const EdgeInsets.fromLTRB(24, 24, 24, 32),
            children: [
              Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text('System Status',
                        style: GoogleFonts.inter(fontSize: 24, fontWeight: FontWeight.w800, color: AppColors.keyboardDark)),
                    const SizedBox(height: 4),
                    Text('Live status of every component. Background programs start and stop with this app.',
                        style: GoogleFonts.inter(fontSize: 13, color: const Color(0xFF5F6B7A))),
                  ]),
                ),
                const SizedBox(width: 16),
                _summaryPill(),
              ]),
              const SectionHeader('Components'),
              LayoutBuilder(builder: (context, c) {
                final cols = c.maxWidth >= 900 ? 3 : 2;
                const gap = 16.0;
                final w = (c.maxWidth - gap * (cols - 1)) / cols;
                final cards = [
                  _eyeTrackingCard(),
                  _emgCard(),
                  const _ComponentCard(
                    title: 'Signal Processing',
                    subtitle: 'Processing layer',
                    icon: Icons.waves_outlined,
                    status: 'Stub Ready',
                    color: AppColors.statusWarning,
                    active: false,
                  ),
                  _backendCard('AI Prediction', 'Word and sentence suggestions', Icons.auto_awesome_outlined),
                  _backendCard('Text-to-Speech', 'Speech output', Icons.surround_sound_outlined,
                      offlineText: 'Windows voice only'),
                  const _ComponentCard(
                    title: 'Cloud Sync',
                    subtitle: 'Cloud layer',
                    icon: Icons.cloud_outlined,
                    status: 'Not Configured',
                    color: AppColors.statusOffline,
                    active: false,
                  ),
                ];
                return Wrap(spacing: gap, runSpacing: gap, children: [
                  for (final card in cards) SizedBox(width: w, height: 150, child: card),
                ]);
              }),
              const SectionHeader('Background programs'),
              SettingCard(children: [
                for (final (i, s) in SystemProcesses.instance.all.indexed) ...[
                  if (i > 0) const Divider(height: 1),
                  _ServiceRow(s),
                ],
              ]),
              Padding(
                padding: const EdgeInsets.fromLTRB(6, 12, 6, 0),
                child: Row(children: [
                  const Icon(Icons.folder_outlined, size: 16, color: Color(0xFF8A96A8)),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                        'Logs: ${SystemProcesses.instance.rootDir ?? 'Combined System'}\\logs  ·  '
                        'paths: system_config.json',
                        overflow: TextOverflow.ellipsis,
                        style: GoogleFonts.inter(fontSize: 12, color: const Color(0xFF8A96A8))),
                  ),
                ]),
              ),
            ],
          ),
        ),
      ),
    );
  }

  /// Overall state of the background programs (same states as the rows below).
  static Widget _summaryPill() {
    final all = SystemProcesses.instance.all;
    final starting = all.where((s) => s.state == ServiceState.starting).length;
    final bad = all.where((s) => s.state == ServiceState.failed || s.state == ServiceState.notStarted).length;
    final (String text, Color col, IconData icon) = bad > 0
        ? ('${bad == 1 ? '1 program needs' : '$bad programs need'} attention', AppColors.statusOffline, Icons.error_outline)
        : starting > 0
            ? ('Starting…', AppColors.statusWarning, Icons.hourglass_top)
            : ('All background programs running', AppColors.statusOnline, Icons.check_circle_outline);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 9),
      decoration: BoxDecoration(
        color: col.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: col.withValues(alpha: 0.35)),
      ),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(icon, color: col, size: 18),
        const SizedBox(width: 8),
        Text(text, style: GoogleFonts.inter(fontSize: 13, fontWeight: FontWeight.w700, color: col)),
      ]),
    );
  }

  static Widget _emgCard() {
    final emg = TrackerClient.instance.status?.emg;
    final (String text, Color col) = emg == null
        ? ('Not Running', AppColors.statusOffline)
        : !emg.enabled
            ? ('Off', const Color(0xFF9AA5B4))
            : emg.ok
                ? ('Connected', AppColors.statusOnline)
                : emg.guard
                    ? ('Check electrodes', AppColors.statusWarning)
                    : emg.state == 'connecting' || emg.state == 'searching'
                        ? ('Connecting', AppColors.statusWarning)
                        : ('Not Connected', AppColors.statusOffline);
    return _ComponentCard(
      title: 'EMG Muscle Sensor',
      subtitle: emg != null && emg.enabled ? 'Input layer · squeeze = Space · ${emg.text}' : 'Input layer · squeeze = Space',
      icon: Icons.sensors,
      status: text,
      color: col,
      active: emg != null && emg.enabled,
    );
  }

  static Widget _eyeTrackingCard() {
    final st = TrackerClient.instance.status;
    final svc = SystemProcesses.instance.tracker;
    String text;
    Color col;
    if (st == null) {
      text = svc.state == ServiceState.starting ? 'Starting' : 'Not Running';
      col = svc.state == ServiceState.starting ? AppColors.statusWarning : AppColors.statusOffline;
    } else {
      final on = ['left', 'right'].where((e) => st.eye(e).enabled).toList();
      final tracking = on.where((e) => st.eye(e).tracking).length;
      text = st.activity != null
          ? 'Calibrating'
          : '$tracking/${on.length} eyes · ${st.mouseEnabled ? 'mouse on' : 'mouse off'}';
      col = tracking == on.length ? AppColors.statusOnline : AppColors.statusWarning;
    }
    return _ComponentCard(
      title: 'Eye Tracking',
      subtitle: st != null && st.calibrated ? 'Input layer · calibration grade ${st.gradeLetter}' : 'Input layer',
      icon: Icons.remove_red_eye_outlined,
      status: text,
      color: col,
    );
  }

  static Widget _backendCard(String title, String subtitle, IconData icon, {String offlineText = 'Not Running'}) {
    final s = SystemProcesses.instance.backend;
    final (String text, Color col) = s.up
        ? ('Active', AppColors.statusOnline)
        : s.state == ServiceState.starting
            ? ('Loading models', AppColors.statusWarning)
            : (offlineText, AppColors.statusOffline);
    return _ComponentCard(title: title, subtitle: subtitle, icon: icon, status: text, color: col);
  }
}

class _ServiceRow extends StatelessWidget {
  final ManagedService s;
  const _ServiceRow(this.s);

  @override
  Widget build(BuildContext context) {
    final (String text, Color col) = switch (s.state) {
      ServiceState.running => ('Running', AppColors.statusOnline),
      ServiceState.external => ('Running (external)', AppColors.statusOnline),
      ServiceState.starting => ('Starting', AppColors.statusWarning),
      ServiceState.disabled => ('Disabled', Colors.grey),
      ServiceState.failed => ('Failed', AppColors.statusOffline),
      ServiceState.notStarted => ('Stopped', Colors.grey),
    };
    final canRestart = s.state == ServiceState.running || s.state == ServiceState.failed || s.state == ServiceState.notStarted;
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 18, vertical: 8),
      leading: MenuIcon(s.name == 'tracker' ? Icons.remove_red_eye_outlined : Icons.memory, AppColors.primaryBlue),
      title: Row(children: [
        Flexible(child: Text(s.label, overflow: TextOverflow.ellipsis)),
        const SizedBox(width: 10),
        StatusPill(text, col),
      ]),
      subtitle: Padding(padding: const EdgeInsets.only(top: 3), child: Text(s.detail)),
      trailing: canRestart
          ? OutlinedButton.icon(
              onPressed: () => SystemProcesses.instance.restart(s),
              style: OutlinedButton.styleFrom(
                foregroundColor: AppColors.primaryBlue,
                side: const BorderSide(color: Color(0xFFC9D6EA)),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(9)),
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
              ),
              icon: const Icon(Icons.refresh, size: 18),
              label: Text('Restart', style: GoogleFonts.inter(fontWeight: FontWeight.w600)),
            )
          : null,
    );
  }
}

class _ComponentCard extends StatelessWidget {
  final String title, subtitle, status;
  final IconData icon;
  final Color color;
  final bool active; // false = not part of this version yet (shown muted)
  const _ComponentCard({
    required this.title,
    required this.subtitle,
    required this.icon,
    required this.status,
    required this.color,
    this.active = true,
  });

  @override
  Widget build(BuildContext context) {
    final iconColor = active ? AppColors.primaryBlue : const Color(0xFF9AA5B4);
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: active ? Colors.white : const Color(0xFFF7F9FC),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: const Color(0xFFE1E8F2)),
        boxShadow: active ? const [BoxShadow(color: Color(0x0A1A2E52), blurRadius: 10, offset: Offset(0, 2))] : null,
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          MenuIcon(icon, iconColor),
          const SizedBox(width: 12),
          // badge pinned to the right edge (Spacer + Flexible split the space and left it floating mid-card)
          Expanded(child: Align(alignment: Alignment.centerRight, child: StatusPill(status, color))),
        ]),
        const SizedBox(height: 14),
        Text(title,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: GoogleFonts.inter(
                fontSize: 15.5, fontWeight: FontWeight.w700, color: active ? AppColors.keyboardDark : const Color(0xFF6B7788))),
        const SizedBox(height: 3),
        Text(subtitle,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: GoogleFonts.inter(fontSize: 12.5, color: const Color(0xFF5F6B7A), height: 1.35)),
      ]),
    );
  }
}
