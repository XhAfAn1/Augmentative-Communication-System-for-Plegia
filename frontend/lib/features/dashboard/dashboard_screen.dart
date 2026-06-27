import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:google_fonts/google_fonts.dart';
import '../../core/theme/app_colors.dart';

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
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('System Status',
                style: GoogleFonts.inter(fontSize: 22, fontWeight: FontWeight.w800, color: AppColors.keyboardDark)),
            const SizedBox(height: 4),
            Text('All component statuses shown below. Next phases will activate these modules.',
                style: GoogleFonts.inter(fontSize: 13, color: Colors.black54)),
            const SizedBox(height: 20),
            Expanded(
              child: GridView.count(
                crossAxisCount: 3,
                crossAxisSpacing: 14,
                mainAxisSpacing: 14,
                childAspectRatio: 1.6,
                children: const [
                  _ComponentCard(
                    title: 'Eye Tracking',
                    subtitle: 'Input Layer',
                    icon: Icons.remove_red_eye_outlined,
                    status: 'Not Connected',
                    color: AppColors.statusOffline,
                    phase: 'Phase 2',
                  ),
                  _ComponentCard(
                    title: 'EEG / BCI',
                    subtitle: 'Input Layer',
                    icon: Icons.psychology_outlined,
                    status: 'Not Connected',
                    color: AppColors.statusOffline,
                    phase: 'Phase 2',
                  ),
                  _ComponentCard(
                    title: 'Signal Processing',
                    subtitle: 'Processing Layer',
                    icon: Icons.waves_outlined,
                    status: 'Stub Ready',
                    color: AppColors.statusWarning,
                    phase: 'Phase 3',
                  ),
                  _ComponentCard(
                    title: 'AI Prediction',
                    subtitle: 'AI Engine',
                    icon: Icons.auto_awesome_outlined,
                    status: 'Stub Ready',
                    color: AppColors.statusWarning,
                    phase: 'Phase 3',
                  ),
                  _ComponentCard(
                    title: 'Text-to-Speech',
                    subtitle: 'Output Layer',
                    icon: Icons.surround_sound_outlined,
                    status: 'Active',
                    color: AppColors.statusOnline,
                    phase: 'Phase 1',
                  ),
                  _ComponentCard(
                    title: 'Cloud Sync',
                    subtitle: 'Cloud Layer',
                    icon: Icons.cloud_outlined,
                    status: 'Not Configured',
                    color: AppColors.statusOffline,
                    phase: 'Phase 4',
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ComponentCard extends StatelessWidget {
  final String title, subtitle, status, phase;
  final IconData icon;
  final Color color;
  const _ComponentCard({
    required this.title,
    required this.subtitle,
    required this.icon,
    required this.status,
    required this.color,
    required this.phase,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      elevation: 0,
      color: Colors.white,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: BorderSide(color: color.withOpacity(0.3)),
      ),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(icon, color: AppColors.primaryBlue, size: 26),
                const Spacer(),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: color.withOpacity(0.12),
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.circle, color: color, size: 8),
                      const SizedBox(width: 4),
                      Text(status,
                          style: GoogleFonts.inter(fontSize: 11, color: color, fontWeight: FontWeight.w700)),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            Text(title, style: GoogleFonts.inter(fontSize: 15, fontWeight: FontWeight.w700, color: AppColors.keyboardDark)),
            Text(subtitle, style: GoogleFonts.inter(fontSize: 11, color: Colors.black54)),
            const Spacer(),
            Text(phase,
                style: GoogleFonts.inter(
                    fontSize: 11, fontWeight: FontWeight.w600, color: AppColors.primaryBlue)),
          ],
        ),
      ),
    );
  }
}
