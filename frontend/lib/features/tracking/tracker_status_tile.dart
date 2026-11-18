import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import '../../core/services/tracker_client.dart';
import '../../core/theme/app_colors.dart';

/// Eye icon for the "Other Functions" header bar (same style as the settings gear). A small dot shows the
/// eye tracker: green = all used eyes tracking, amber = searching / calibrating, red = camera lost, grey = offline.
/// The tooltip gives the details; clicking opens the Eye Tracking screen.
class TrackerHeaderButton extends StatelessWidget {
  const TrackerHeaderButton({super.key});

  @override
  Widget build(BuildContext context) {
    final t = TrackerClient.instance;
    return ListenableBuilder(
      listenable: t,
      builder: (context, _) {
        final st = t.status;
        Color dot;
        String tip;
        if (st == null) {
          dot = Colors.grey;
          tip = 'Eye tracker offline';
        } else {
          final used = ['left', 'right'].where((e) => st.eye(e).enabled).map(st.eye).toList();
          final lost = used.any((e) => !e.connected);
          final tracking = used.where((e) => e.tracking).length;
          dot = st.activity != null
              ? AppColors.statusWarning
              : lost
                  ? AppColors.statusOffline
                  : tracking == used.length
                      ? AppColors.statusOnline
                      : AppColors.statusWarning;
          tip = st.activity != null
              ? 'Eye tracking: calibrating'
              : 'Eye tracking: $tracking/${used.length} eyes · mouse ${st.mouseEnabled ? 'on' : 'off'} · '
                  '${st.calibrated ? 'grade ${st.gradeLetter}' : 'not calibrated'}';
        }
        return Tooltip(
          message: tip,
          child: IconButton(
            onPressed: () => context.push('/tracking'),
            padding: EdgeInsets.zero,
            constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
            icon: Stack(clipBehavior: Clip.none, children: [
              const Icon(Icons.remove_red_eye_outlined, color: Colors.white70, size: 18),
              Positioned(
                right: -3,
                top: -3,
                child: Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(
                      color: dot, shape: BoxShape.circle, border: Border.all(color: AppColors.panelHeader, width: 1.2)),
                ),
              ),
            ]),
          ),
        );
      },
    );
  }
}
