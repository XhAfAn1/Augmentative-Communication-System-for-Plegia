import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:google_fonts/google_fonts.dart';
import '../../core/services/system_processes.dart';
import '../../core/services/tracker_client.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/ui_kit.dart';
import 'emg_card.dart';
import '../../core/services/gaze_targets.dart';

/// Eye Tracking control panel: everything the Python launcher offered (Sensors, Calibration, Mouse), talking to
/// the background eye tracker. Calibration, fine-tune, re-centre and Eye View still open the tracker's own windows.
class TrackingScreen extends StatefulWidget {
  final int initialTab;
  const TrackingScreen({super.key, this.initialTab = 0});

  @override
  State<TrackingScreen> createState() => _TrackingScreenState();
}

class _TrackingScreenState extends State<TrackingScreen> {
  final TrackerClient _t = TrackerClient.instance;

  @override
  void initState() {
    super.initState();
    _t.useFastPolling(true);
  }

  @override
  void dispose() {
    _t.useFastPolling(false);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 3,
      initialIndex: widget.initialTab.clamp(0, 2),
      child: Scaffold(
        backgroundColor: AppColors.appBackground,
        appBar: AppBar(
          backgroundColor: AppColors.keyboardDark,
          foregroundColor: Colors.white,
          title: Text('Eye Tracking', style: GoogleFonts.inter(fontWeight: FontWeight.w700)),
          leading: IconButton(icon: const Icon(Icons.arrow_back_ios_new), onPressed: () => context.pop()),
          actions: [
            TextButton.icon(
              onPressed: () => context.push('/dashboard'),
              icon: const Icon(Icons.dashboard_outlined, color: Colors.white70, size: 18),
              label: Text('System Status', style: GoogleFonts.inter(color: Colors.white70, fontSize: 12)),
            ),
            const SizedBox(width: 8),
          ],
          bottom: TabBar(
            labelColor: Colors.white,
            unselectedLabelColor: Colors.white60,
            indicatorColor: AppColors.accentCyan,
            labelStyle: GoogleFonts.inter(fontWeight: FontWeight.w700),
            tabs: const [
              Tab(icon: Icon(Icons.remove_red_eye_outlined), text: 'Sensors'),
              Tab(icon: Icon(Icons.center_focus_strong_outlined), text: 'Calibration'),
              Tab(icon: Icon(Icons.mouse_outlined), text: 'Mouse'),
            ],
          ),
        ),
        body: MenuPage(child: ListenableBuilder(
          listenable: Listenable.merge([_t, SystemProcesses.instance]),
          builder: (context, _) {
            final st = _t.status;
            return Column(children: [
              if (!_t.online || st == null) const _OfflineBanner(),
              Expanded(
                child: st == null
                    ? const SizedBox.shrink()
                    : TabBarView(children: [_SensorsTab(st), _CalibrationTab(st), _MouseTab(st)]),
              ),
            ]);
          },
        )),
      ),
    );
  }
}

// ═══════════════════════════════════════════════════════════════════════════ offline
class _OfflineBanner extends StatelessWidget {
  const _OfflineBanner();

  @override
  Widget build(BuildContext context) {
    final s = SystemProcesses.instance.tracker;
    final starting = s.state == ServiceState.starting || s.state == ServiceState.notStarted;
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.fromLTRB(20, 20, 20, 0),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: (starting ? AppColors.statusWarning : AppColors.statusOffline).withValues(alpha: 0.5)),
      ),
      child: Row(children: [
        starting
            ? const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5))
            : const Icon(Icons.error_outline, color: AppColors.statusOffline),
        const SizedBox(width: 14),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(starting ? 'Starting the eye tracker…' : 'The eye tracker is not running',
                style: GoogleFonts.inter(fontWeight: FontWeight.w700, color: AppColors.keyboardDark)),
            Text(s.detail.isEmpty ? 'Waiting for the tracker' : s.detail,
                style: GoogleFonts.inter(fontSize: 12, color: Colors.black54)),
          ]),
        ),
        if (!starting && s.state != ServiceState.external && s.state != ServiceState.disabled)
          ElevatedButton.icon(
            onPressed: () => SystemProcesses.instance.restart(s),
            icon: const Icon(Icons.refresh),
            label: const Text('Restart'),
          ),
      ]),
    );
  }
}

// ═══════════════════════════════════════════════════════════════════════════ sensors
class _SensorsTab extends StatelessWidget {
  final TrackerStatus st;
  const _SensorsTab(this.st);

  @override
  Widget build(BuildContext context) {
    final t = TrackerClient.instance;
    return ListView(padding: const EdgeInsets.fromLTRB(20, 0, 20, 20), children: [
      const SectionHeader('CAMERAS'),
      Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Expanded(child: _EyeCard(st, 'left')),
        const SizedBox(width: 12),
        Expanded(child: _EyeCard(st, 'right')),
      ]),
      const SectionHeader('PUPIL DETECTION'),
      SettingCard(children: [
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          child: Row(children: [
            const Icon(Icons.track_changes, color: AppColors.primaryBlue),
            const SizedBox(width: 16),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('Detection Method', style: GoogleFonts.inter(fontWeight: FontWeight.w600, fontSize: 14)),
                Text('ArcFit is the tuned default; Orlosky is the fallback',
                    style: GoogleFonts.inter(fontSize: 12, color: Colors.grey[600])),
              ]),
            ),
            DropdownButton<String>(
              value: st.algorithms.any((a) => a['id'] == st.algorithm) ? st.algorithm : null,
              underline: const SizedBox(),
              items: [
                for (final a in st.algorithms)
                  DropdownMenuItem(value: a['id'], child: Text(a['label']!, style: GoogleFonts.inter(fontSize: 13))),
              ],
              onChanged: (v) => v == null ? null : t.setAlgorithm(v),
            ),
          ]),
        ),
        const Divider(),
        _LiveSlider(
          icon: Icons.blur_on,
          value: st.pupilSmoothing,
          min: 0,
          max: 10,
          divisions: 10,
          title: (v) => 'Pupil Smoothing: ${v.round()}${v.round() == 3 ? '  (default)' : ''}',
          hint: 'Steadier pupil centre at higher values; quick eye movements are never smoothed away',
          onCommit: (v) => t.setPupilSmoothing(v.roundToDouble()),
        ),
      ]),
      const SectionHeader('CAMERA IMAGE & EYE AREA'),
      SettingCard(children: [
        ListTile(
          leading: const Icon(Icons.videocam_outlined, color: AppColors.primaryBlue),
          title: Text('Eye View', style: GoogleFonts.inter(fontWeight: FontWeight.w600)),
          subtitle: Text(
              'Live camera images with the detected pupil, eye-area (cage) setup and 10 s recording. '
              'Opens in the tracker\'s own window.',
              style: GoogleFonts.inter(fontSize: 12)),
          trailing: ElevatedButton.icon(
            onPressed: () => t.openEyeView(),
            icon: Icon(st.eyeViewOpen ? Icons.open_in_new : Icons.visibility_outlined),
            label: Text(st.eyeViewOpen ? 'Show' : 'Open'),
          ),
        ),
      ]),
      const SectionHeader('EMG MUSCLE SENSOR'),
      const EmgCard(),
    ]);
  }
}

class _EyeCard extends StatelessWidget {
  final TrackerStatus st;
  final String eye;
  const _EyeCard(this.st, this.eye);

  @override
  Widget build(BuildContext context) {
    final t = TrackerClient.instance;
    final e = st.eye(eye);
    final other = st.eye(eye == 'left' ? 'right' : 'left');
    final name = eye == 'left' ? 'Left Eye' : 'Right Eye';
    final (String label, Color col) = !e.enabled
        ? ('Disabled', Colors.grey)
        : !e.connected
            ? ('Disconnected', AppColors.statusOffline)
            : e.pupil == null
                ? ('Searching', AppColors.statusWarning)
                : ('Tracking', AppColors.statusOnline);
    // the last eye in use cannot be switched off (the tracker refuses it too)
    final lastEye = e.enabled && !other.enabled;
    return SettingCard(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 12, 14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(Icons.remove_red_eye_outlined, color: eye == 'left' ? AppColors.accentCyan : AppColors.statusWarning),
            const SizedBox(width: 10),
            Text(name,
                style: GoogleFonts.inter(fontWeight: FontWeight.w700, fontSize: 15, color: AppColors.keyboardDark)),
            const SizedBox(width: 10),
            StatusPill(label, col),
            const Spacer(),
            Tooltip(
              message: lastEye ? 'The other eye is off' : (e.enabled ? 'Use this eye: on' : 'Use this eye: off'),
              child: Switch(value: e.enabled, onChanged: lastEye ? null : (v) => t.setEyeEnabled(eye, v)),
            ),
          ]),
          const SizedBox(height: 4),
          Row(children: [
            Expanded(
              child: Text(
                e.enabled && e.connected
                    ? '${e.fps.toStringAsFixed(0)} FPS · detection ${e.detFps.toStringAsFixed(0)} Hz'
                        '${e.pupil != null ? ' · confidence ${(e.conf * 100).toStringAsFixed(0)} %' : ''}'
                    : e.enabled
                        ? 'No image'
                        : 'Off · the other eye tracks alone',
                style: GoogleFonts.inter(fontSize: 12, color: Colors.black54),
              ),
            ),
            DropdownButton<int>(
              value: e.cameraId,
              underline: const SizedBox(),
              isDense: true,
              style: GoogleFonts.inter(fontSize: 13, color: AppColors.keyboardDark),
              items: [
                for (final c in const [0, 1, 2])
                  DropdownMenuItem(
                    value: c,
                    child: Text('Camera $c${other.enabled && other.cameraId == c && c != e.cameraId ? ' (swap)' : ''}'),
                  ),
              ],
              onChanged: e.enabled ? (v) => v == null ? null : t.setCamera(eye, v) : null,
            ),
          ]),
        ]),
      ),
    ]);
  }
}

// ═══════════════════════════════════════════════════════════════════════════ calibration
class _CalibrationTab extends StatelessWidget {
  final TrackerStatus st;
  const _CalibrationTab(this.st);

  String _px(double? v) => v == null ? 'n/a' : '${v.toStringAsFixed(1)} px';

  @override
  Widget build(BuildContext context) {
    final t = TrackerClient.instance;
    final busy = st.activity != null;
    final grade = st.gradeLetter;
    final gradeCol = !st.calibrated
        ? AppColors.statusWarning
        : (grade == 'A' || grade == 'B')
            ? AppColors.statusOnline
            : grade == 'C'
                ? AppColors.statusWarning
                : AppColors.statusOffline;
    final off = st.offset;
    return ListView(padding: const EdgeInsets.fromLTRB(20, 0, 20, 20), children: [
      const SectionHeader('CURRENT CALIBRATION'),
      SettingCard(children: [
        Padding(
          padding: const EdgeInsets.all(16),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Container(
              width: 64,
              height: 64,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                  color: gradeCol.withValues(alpha: 0.12), borderRadius: BorderRadius.circular(14)),
              child: Text(grade, style: GoogleFonts.inter(fontSize: 30, fontWeight: FontWeight.w800, color: gradeCol)),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(st.calibrated ? st.grade : 'Not calibrated yet',
                    style: GoogleFonts.inter(fontWeight: FontWeight.w700, fontSize: 15, color: AppColors.keyboardDark)),
                const SizedBox(height: 4),
                Text(
                    st.calibrated
                        ? 'Mean error ${_px(st.meanErrorPx)}  ·  Pursuit RMS ${_px(st.pursuitRmse)}  ·  '
                            'Jitter ${_px(st.meanJitter)}\n${st.calibratedAt}'
                            '${off != null && (off[0] != 0 || off[1] != 0) ? '  ·  slip correction (${(off[0] as num).toStringAsFixed(0)}, ${(off[1] as num).toStringAsFixed(0)}) px' : ''}'
                        : 'Run a full calibration after putting the headset on.',
                    style: GoogleFonts.inter(fontSize: 12, color: Colors.black54, height: 1.4)),
              ]),
            ),
            const SizedBox(width: 12),
            SizedBox(
              height: 64,
              child: Center(
                child: Tooltip(
                  message: 'Dots + your live gaze, to find your calibration position · changes nothing',
                  child: OutlinedButton.icon(
                    onPressed: busy || !st.calibrated ? null : () => t.startPositionGuide(),
                    icon: const Icon(Icons.accessibility_new, size: 18),
                    label: const Text('Position Guide'),
                  ),
                ),
              ),
            ),
          ]),
        ),
      ]),
      if (busy)
        Padding(
          padding: const EdgeInsets.only(top: 12),
          child: Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: AppColors.accentCyan.withValues(alpha: 0.08),
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: AppColors.accentCyan.withValues(alpha: 0.4)),
            ),
            child: Row(children: [
              const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2.5)),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                    '${_activityName(st.activity!)} is running in its full-screen window. '
                    'ESC there cancels and keeps the previous calibration.',
                    style: GoogleFonts.inter(fontSize: 13, color: AppColors.keyboardDark)),
              ),
            ]),
          ),
        ),
      const SectionHeader('CALIBRATE'),
      Row(children: [
        Expanded(
          child: DwellActionButton(
            label: 'Full Calibration',
            sublabel: '9 dots, line tracking, checks · after putting the headset on',
            icon: Icons.center_focus_strong,
            color: AppColors.primaryBlue,
            onTap: busy ? null : () => t.startCalibration(),
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: DwellActionButton(
            label: 'Level 2 Fine-tune',
            sublabel: st.canFineTune ? '13 points on your keyboard · press Space at each' : 'Needs a full calibration first',
            icon: Icons.tune,
            color: AppColors.accentCyan,
            onTap: busy || !st.canFineTune ? null : () => t.startFineTune(GazeTargets.collect()),
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: DwellActionButton(
            label: 'Quick Re-centre',
            sublabel: st.calibrated ? '5 dots, about 8 s · corrects a small headset slip' : 'Needs a calibration first',
            icon: Icons.filter_center_focus,
            color: AppColors.saveBtn,
            onTap: busy || !st.calibrated ? null : () => t.startRecentre(),
          ),
        ),
      ]),
      const SizedBox(height: 10),
      SettingCard(children: [
        SwitchTile(
          title: 'Show gaze pointer',
          subtitle: 'Pointer of your live gaze in full calibration, Level 2 fine-tune and Quick Re-centre',
          value: st.showGazePointer,
          onChanged: (v) => t.setShowGazePointer(v),
          icon: Icons.my_location,
          iconColor: AppColors.accentCyan,
        ),
        SwitchTile(
          title: 'Dark calibration background',
          subtitle: st.darkCalibrationBackground
              ? 'On: calibration screens are dark. Turn off for a light background (same as the app) to compare accuracy'
              : 'Off: calibration screens are light like the app. Turn on for the dark background to compare accuracy',
          value: st.darkCalibrationBackground,
          onChanged: busy ? null : (v) => t.setDarkCalibrationBackground(v),
          icon: Icons.dark_mode_outlined,
          iconColor: AppColors.primaryBlue,
        ),
        SwitchTile(
          title: 'End shift correction',
          subtitle: st.endShift == null
              ? 'No end shift stored (none measured, or replaced by Quick Re-centre / fine-tune)'
              : 'Shift (${st.endShift![0]}, ${st.endShift![1]}) px measured at the end of the calibration'
                  '${st.endShiftBeforePx != null && st.endShiftAfterPx != null ? ' · check dots ${st.endShiftBeforePx!.round()} px, with it ${st.endShiftAfterPx!.round()} px' : ''}'
                  ' · ${st.endShiftOn ? 'On: cursor uses it' : 'Off: cursor without it'}',
          value: st.endShiftOn,
          onChanged: (v) => t.setEndShift(v),
          icon: Icons.open_with,
          iconColor: AppColors.primaryBlue,
        ),
        SwitchTile(
          title: 'Auto re-centre while using',
          subtitle: st.autoRecentre
              ? (st.autoRecentreCount == 0
                  ? 'On: learns the headset slip from the buttons you select (nothing learned yet)'
                  : st.autoRecentreAreas != null
                  ? 'On: ${st.autoRecentreCount} selections, small corrections in ${st.autoRecentreAreas} screen '
                      'areas (temporary, never saved) since the last calibration'
                  : 'On: learned from ${st.autoRecentreCount} selections (fitted to the last 5): shift '
                      '(${st.autoRecentreShift[0]}, ${st.autoRecentreShift[1]}) px, scale '
                      '${(st.autoRecentreScale[0] * 100).round()}% x ${(st.autoRecentreScale[1] * 100).round()}% '
                      'since the last calibration')
              : 'Learns the headset slip from the buttons you select, so the cursor stays on target',
          value: st.autoRecentre,
          onChanged: (v) => t.setAutoRecentre(v),
          icon: Icons.center_focus_weak,
          iconColor: AppColors.saveBtn,
        ),
        SwitchTile(
          title: 'Record eye video during calibration',
          subtitle: 'Saves raw eye frames in the tracker\'s recordings folder for offline analysis',
          value: st.recordCalibration,
          onChanged: (v) => t.setRecordCalibration(v),
          icon: Icons.fiber_manual_record_outlined,
          iconColor: AppColors.clearBtn,
        ),
      ]),
      const SectionHeader('DIAGNOSTIC CHART'),
      SettingCard(children: [
        Padding(
          padding: const EdgeInsets.all(12),
          child: st.chartVersion == null
              ? Padding(
                  padding: const EdgeInsets.all(20),
                  child: Text('No chart yet. It is made after each calibration.',
                      style: GoogleFonts.inter(color: Colors.black54)),
                )
              : ClipRRect(
                  borderRadius: BorderRadius.circular(8),
                  child: Image.network(
                    t.chartUrl(st.chartVersion),
                    key: ValueKey(st.chartVersion),
                    height: 320,
                    fit: BoxFit.contain,
                    gaplessPlayback: true,
                    errorBuilder: (_, __, ___) =>
                        Text('Chart could not be loaded', style: GoogleFonts.inter(color: Colors.black54)),
                  ),
                ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
          child: Align(
            alignment: Alignment.centerRight,
            child: OutlinedButton.icon(
              onPressed: st.chartVersion == null ? null : () => t.openChart(),
              icon: const Icon(Icons.open_in_full),
              label: const Text('Open Full Chart'),
            ),
          ),
        ),
      ]),
    ]);
  }

  static String _activityName(String a) =>
      {'calibration': 'Full calibration', 'finetune': 'Level 2 fine-tune', 'recentre': 'Quick re-centre', 'position': 'Position guide'}[a] ?? a;
}

// ═══════════════════════════════════════════════════════════════════════════ mouse
class _MouseTab extends StatelessWidget {
  final TrackerStatus st;
  const _MouseTab(this.st);

  @override
  Widget build(BuildContext context) {
    final t = TrackerClient.instance;
    final smoother = st.mouseMode == 'smoother';
    final cur = st.cursor;
    final lp = st.eye('left').pupil, rp = st.eye('right').pupil;
    String pp(List? p) => p == null ? 'no signal' : '(${(p[0] as num).toStringAsFixed(0)}, ${(p[1] as num).toStringAsFixed(0)})';
    return ListView(padding: const EdgeInsets.fromLTRB(20, 0, 20, 20), children: [
      const SectionHeader('EYE MOUSE'),
      DwellActionButton(
        label: st.mouseEnabled ? 'Disable Eye Mouse Control  (F6)' : 'Enable Eye Mouse Control  (F5)',
        sublabel: 'F5 toggles · F6 stops at once · moving the real mouse into the top-left corner also stops it',
        icon: st.mouseEnabled ? Icons.stop_circle_outlined : Icons.play_circle_outline,
        color: st.mouseEnabled ? AppColors.clearBtn : AppColors.primaryBlue,
        onTap: () => t.setMouseEnabled(!st.mouseEnabled),
        height: 70,
      ),
      if (st.failsafeTripped)
        Padding(
          padding: const EdgeInsets.only(top: 8),
          child: Text('Fail-safe: the physical mouse reached the top-left corner, so mouse control was switched off.',
              style: GoogleFonts.inter(fontSize: 12, color: AppColors.statusOffline, fontWeight: FontWeight.w600)),
        ),
      const SectionHeader('MOVEMENT MODE'),
      SettingCard(children: [
        Padding(
          padding: const EdgeInsets.all(14),
          child: Wrap(spacing: 8, children: [
            for (final m in const [
              ['smoother', 'GazeSmoother (classic)'],
              ['filtered', '1-Euro Filter'],
              ['raw', 'Raw Direct'],
            ])
              ChoiceChip(
                label: Text(m[1]),
                selected: st.mouseMode == m[0],
                selectedColor: AppColors.primaryBlueLight.withValues(alpha: 0.2),
                labelStyle: GoogleFonts.inter(
                    fontWeight: FontWeight.w600,
                    color: st.mouseMode == m[0] ? AppColors.primaryBlue : Colors.black87),
                onSelected: (_) => t.setMouseMode(m[0]),
              ),
          ]),
        ),
      ]),
      Row(children: [
        const SectionHeader('CURSOR TUNING'),
        const Spacer(),
        TextButton.icon(
          onPressed: () => _confirmReset(context),
          icon: const Icon(Icons.restart_alt, size: 18),
          label: Text('Reset to defaults', style: GoogleFonts.inter(fontSize: 12)),
        ),
      ]),
      SettingCard(children: [
        _LiveSlider(
          icon: Icons.waves,
          value: st.alpha,
          min: 0.01,
          max: 0.50,
          divisions: 49,
          enabled: smoother,
          title: (v) => 'Smoothing Alpha: ${v.toStringAsFixed(2)}',
          hint: 'Lower = calmer and steadier, higher = faster. Also used by the calibration pointer.',
          onCommit: (v) => t.setMouseParams({'alpha': double.parse(v.toStringAsFixed(2))}),
        ),
        const Divider(),
        _LiveSlider(
          icon: Icons.open_with,
          value: st.gain,
          min: 0.5,
          max: 5.0,
          divisions: 45,
          enabled: smoother,
          title: (v) => 'Cursor Gain: ${v.toStringAsFixed(1)}×',
          hint: '1.0× = true 1:1 calibrated gaze',
          onCommit: (v) => t.setMouseParams({'gain': double.parse(v.toStringAsFixed(1))}),
        ),
        const Divider(),
        _LiveSlider(
          icon: Icons.monitor,
          value: st.dpi,
          min: 30,
          max: 192,
          divisions: 162,
          enabled: smoother,
          title: (v) => 'Monitor DPI Scale: ${v.round()} DPI  (effective gain ${(st.gain * v / 96).toStringAsFixed(2)}×)',
          hint: 'Zero distortion at effective gain 1.00×',
          onCommit: (v) => t.setMouseParams({'dpi': v.roundToDouble()}),
        ),
        const Divider(),
        _LiveSlider(
          icon: Icons.visibility_off_outlined,
          value: st.graceMs.toDouble(),
          min: 50,
          max: 600,
          divisions: 55,
          enabled: smoother,
          title: (v) => 'Blink Grace Period: ${v.round()} ms',
          hint: 'Holds the cursor still during blinks',
          onCommit: (v) => t.setMouseParams({'grace_ms': v.round()}),
        ),
        const Divider(),
        SwitchTile(
          title: 'Spacebar Click (system-wide)',
          subtitle: st.spacebarPaused
              ? 'Paused while this app is in front: the app\'s own Spacebar Select is used, so one press selects once'
              : 'Press Spacebar anywhere to left-click at the gaze cursor',
          value: t.pendingSpacebarClick ?? st.spacebarClick, // same switch as Settings > Spacebar Select
          onChanged: (v) => t.setSpacebarClick(v),
          icon: Icons.space_bar,
          iconColor: const Color(0xFF22c55e),
        ),
        const Divider(),
        SwitchTile(
          title: 'Edge Reach',
          subtitle: st.edgeReach
              ? 'On: easier to reach the screen edges'
              : 'Off: cursor exactly where you look (more accurate)',
          value: st.edgeReach,
          onChanged: (v) => t.setMouseParams({'edge_reach': v}),
          icon: Icons.fit_screen,
          iconColor: AppColors.primaryBlue,
        ),
      ]),
      const SectionHeader('LIVE TELEMETRY'),
      SettingCard(children: [
        Padding(
          padding: const EdgeInsets.all(16),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Text(cur == null ? 'Screen cursor: no signal' : 'Screen cursor: (${cur[0]}, ${cur[1]})',
                  style: GoogleFonts.inter(fontWeight: FontWeight.w700, color: AppColors.statusOnline)),
              const Spacer(),
              Text('${st.mouseFps.toStringAsFixed(0)} updates/s', style: GoogleFonts.inter(fontSize: 12, color: Colors.black54)),
            ]),
            const SizedBox(height: 6),
            Text('Status: ${st.cursorState}  ·  Signal: ${st.signalMode}  ·  Effective gain ${st.effectiveGain.toStringAsFixed(2)}×',
                style: GoogleFonts.inter(fontSize: 12, color: AppColors.keyboardDark)),
            const SizedBox(height: 4),
            Text('Left pupil ${pp(lp)}  ·  Right pupil ${pp(rp)}',
                style: GoogleFonts.inter(fontSize: 12, color: Colors.black54)),
          ]),
        ),
      ]),
    ]);
  }

  Future<void> _confirmReset(BuildContext context) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text('Reset cursor tuning?', style: GoogleFonts.inter(fontWeight: FontWeight.w700)),
        content: Text(
            'Sets Smoothing Alpha 0.15, Gain 1.0×, 96 DPI, Blink Grace 240 ms and Spacebar Click on.\n'
            'Your current alpha (${st.alpha.toStringAsFixed(2)}) will be replaced.',
            style: GoogleFonts.inter()),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
          ElevatedButton(onPressed: () => Navigator.pop(c, true), child: const Text('Reset')),
        ],
      ),
    );
    if (ok == true) await TrackerClient.instance.resetMouse();
  }
}

/// Slider that shows its value while dragging and sends it to the tracker once, when released.
class _LiveSlider extends StatefulWidget {
  final IconData icon;
  final double value, min, max;
  final int divisions;
  final bool enabled;
  final String Function(double) title;
  final String? hint;
  final ValueChanged<double> onCommit;
  const _LiveSlider({
    required this.icon,
    required this.value,
    required this.min,
    required this.max,
    required this.divisions,
    required this.title,
    required this.onCommit,
    this.hint,
    this.enabled = true,
  });

  @override
  State<_LiveSlider> createState() => _LiveSliderState();
}

class _LiveSliderState extends State<_LiveSlider> {
  double? _drag;

  @override
  Widget build(BuildContext context) {
    final v = _drag ?? widget.value;
    return SliderTile(
      icon: widget.icon,
      title: widget.title(v),
      hint: widget.hint,
      value: v,
      min: widget.min,
      max: widget.max,
      divisions: widget.divisions,
      onChanged: widget.enabled ? (x) => setState(() => _drag = x) : null,
      onChangeEnd: widget.enabled
          ? (x) {
              widget.onCommit(x);
              Future.delayed(const Duration(milliseconds: 400), () {
                if (mounted) setState(() => _drag = null); // then follow the tracker's value again
              });
            }
          : null,
    );
  }
}
