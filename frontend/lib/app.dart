import 'dart:async';
import 'dart:io' show File, Platform;
import 'dart:ui' show AppExitResponse;
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'core/services/system_processes.dart';
import 'core/services/tracker_client.dart';
import 'core/theme/app_colors.dart';
import 'core/theme/app_theme.dart';
import 'core/widgets/dwell_wrapper.dart' show dwellDurationMs;
import 'features/keyboard/gaze_keyboard.dart' show flutterSpacebarEnabled, flutterDwellEnabled, selectionSoundEnabled;
import 'features/keyboard/keyboard_screen.dart';
import 'features/settings/settings_screen.dart';
import 'features/dashboard/dashboard_screen.dart';
import 'features/tracking/tracking_screen.dart';

class SynapTalkApp extends StatefulWidget {
  const SynapTalkApp({super.key});

  @override
  State<SynapTalkApp> createState() => _SynapTalkAppState();
}

class _SynapTalkAppState extends State<SynapTalkApp> {
  final GlobalKey<ScaffoldMessengerState> _messenger = GlobalKey<ScaffoldMessengerState>();
  late final AppLifecycleListener _lifecycle;
  StreamSubscription<String>? _msgSub;
  bool _inFront = true;

  static final _router = GoRouter(
    initialLocation: '/',
    routes: [
      GoRoute(path: '/', builder: (_, __) => const KeyboardScreen()),
      GoRoute(path: '/settings', builder: (_, __) => const SettingsScreen()),
      GoRoute(path: '/dashboard', builder: (_, __) => const DashboardScreen()),
      GoRoute(path: '/tracking', builder: (_, __) => const TrackingScreen()),
    ],
  );

  @override
  void initState() {
    super.initState();
    _loadPrefs();
    // background programs: AI backend + eye tracker (stopped again in onExitRequested)
    unawaited(SystemProcesses.instance.startAll());
    TrackerClient.instance.start();
    _msgSub = TrackerClient.instance.messages.listen(_showMessage);
    flutterSpacebarEnabled.addListener(_updateSpacebarOwner);
    TrackerClient.instance.addListener(_syncSpacebar);
    _lifecycle = AppLifecycleListener(
      onStateChange: (s) {
        _inFront = s == AppLifecycleState.resumed;
        _updateSpacebarOwner();
      },
      onExitRequested: () async {
        await SystemProcesses.instance.stopAll();
        return AppExitResponse.exit;
      },
    );
    _updateSpacebarOwner();
  }

  SharedPreferences? _prefs;

  /// Opens the saved settings. A power cut during a save can leave the file unreadable (seen 2026-11-21: all
  /// zero bytes); then nothing could load or save again. Such a file is put aside as
  /// shared_preferences.json.corrupt and the app continues with default settings, which save normally again.
  Future<SharedPreferences> _openPrefs() async {
    try {
      return await SharedPreferences.getInstance();
    } catch (e) {
      final appData = Platform.environment['APPDATA'];
      if (appData == null) rethrow;
      // folder = CompanyName\ProductName from windows/runner/Runner.rc (where the settings plugin keeps the file)
      final f = File('$appData\\com.capstone\\synaptalk\\shared_preferences.json');
      if (!f.existsSync()) rethrow;
      var aside = File('${f.path}.corrupt');
      if (aside.existsSync()) aside = File('${f.path}.corrupt_${DateTime.now().millisecondsSinceEpoch}');
      f.renameSync(aside.path);
      debugPrint('Settings file could not be read ($e): moved to ${aside.path}, using default settings');
      return SharedPreferences.getInstance();
    }
  }

  Future<void> _loadPrefs() async {
    try {
      final p = _prefs = await _openPrefs();
      dwellDurationMs.value = p.getInt('dwell_ms') ?? dwellDurationMs.value;
      flutterDwellEnabled.value = p.getBool('dwell_enabled') ?? flutterDwellEnabled.value;
      selectionSoundEnabled.value = p.getBool('selection_sound') ?? selectionSoundEnabled.value;
      // backup copy for when the tracker is not running; the tracker's saved value wins once it is up
      flutterSpacebarEnabled.value = p.getBool('spacebar_select') ?? flutterSpacebarEnabled.value;
      _syncSpacebar();
    } catch (_) {}
    // saved the moment they change (not only at exit, so a crash or power cut keeps them)
    flutterDwellEnabled.addListener(_saveSwitches);
    flutterSpacebarEnabled.addListener(_saveSwitches);
    selectionSoundEnabled.addListener(_saveSwitches);
  }

  void _saveSwitches() {
    unawaited(_prefs?.setBool('dwell_enabled', flutterDwellEnabled.value));
    unawaited(_prefs?.setBool('spacebar_select', flutterSpacebarEnabled.value));
    unawaited(_prefs?.setBool('selection_sound', selectionSoundEnabled.value));
  }

  /// While this window is in front and its own Spacebar Select is on, the tracker's Spacebar click pauses,
  /// so one press selects once. In any other program the tracker's click works as before.
  void _updateSpacebarOwner() {
    TrackerClient.instance.setAppOwnsSpacebar(_inFront && flutterSpacebarEnabled.value);
  }

  /// The app's Spacebar Select follows the tracker's saved Spacebar Click (one setting, two switches),
  /// or the change still being sent to the tracker.
  void _syncSpacebar() {
    final c = TrackerClient.instance;
    final want = c.pendingSpacebarClick ?? c.status?.spacebarClick;
    if (want != null && flutterSpacebarEnabled.value != want) flutterSpacebarEnabled.value = want;
  }

  /// HIDDEN SWITCH (code only): false = the bottom pop-up messages (SnackBars) are not shown (2026-11-23, user).
  /// They are the app's only pop-ups: tracker events (calibration done, re-centre, ...) and tracker errors.
  /// Messages still go to the debug log; set true to show them again.
  static const bool _showPopups = false;

  void _showMessage(String msg) {
    debugPrint('[Message] $msg');
    if (!_showPopups) return;
    _messenger.currentState?.showSnackBar(SnackBar(
      content: Text(msg, style: GoogleFonts.inter(fontWeight: FontWeight.w600)),
      backgroundColor: AppColors.keyboardDark,
      behavior: SnackBarBehavior.floating,
      duration: const Duration(seconds: 6),
    ));
  }

  @override
  void dispose() {
    _lifecycle.dispose();
    _msgSub?.cancel();
    flutterSpacebarEnabled.removeListener(_updateSpacebarOwner);
    TrackerClient.instance.removeListener(_syncSpacebar);
    flutterDwellEnabled.removeListener(_saveSwitches);
    flutterSpacebarEnabled.removeListener(_saveSwitches);
    selectionSoundEnabled.removeListener(_saveSwitches);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: 'AAC System',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.lightTheme,
      scaffoldMessengerKey: _messenger,
      routerConfig: _router,
    );
  }
}
