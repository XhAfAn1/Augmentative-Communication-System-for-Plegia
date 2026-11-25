import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import '../../core/services/tracker_client.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/ui_kit.dart';

/// EMG muscle sensor: on/off switch and the EMG View button together, so both are in one place.
/// The same card is shown in Settings and in Eye Tracking > Sensors.
class EmgCard extends StatelessWidget {
  const EmgCard({super.key});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: TrackerClient.instance,
      builder: (context, _) {
        final t = TrackerClient.instance;
        final st = t.status;
        final emg = st?.emg;
        final usable = emg != null && emg.supported;
        return SettingCard(children: [
          SwitchTile(
            title: 'EMG Sensor Input',
            subtitle: st == null
                ? 'Eye tracker not running'
                : !usable
                    ? 'Not available in this tracker'
                    : 'A squeeze presses Space · ${emg.enabled ? emg.text : 'off'}',
            value: emg?.enabled ?? false,
            onChanged: usable ? (v) => t.setEmgEnabled(v) : null,
            icon: Icons.sensors,
            iconColor: usable ? AppColors.primaryBlue : Colors.grey,
          ),
          const Divider(),
          ListTile(
            leading: const MenuIcon(Icons.show_chart, AppColors.primaryBlue),
            title: Text('EMG View', style: GoogleFonts.inter(fontWeight: FontWeight.w600)),
            subtitle: Text('Live signal, threshold and calibration', style: GoogleFonts.inter(fontSize: 12)),
            trailing: ElevatedButton.icon(
              onPressed: usable ? () => t.openEmgView() : null,
              icon: Icon(st?.emgViewOpen == true ? Icons.open_in_new : Icons.show_chart),
              label: Text(st?.emgViewOpen == true ? 'Show' : 'Open'),
            ),
          ),
        ]);
      },
    );
  }
}
