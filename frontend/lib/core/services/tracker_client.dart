import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'win_native.dart';

/// Connection to the eye tracker (Binocular Eye Tracking Module/tracker_service.py, 127.0.0.1:8001).
/// Polls /status (fast while a tracking screen is open, slow otherwise) and sends commands. The tracker itself
/// keeps running the cameras, the gaze mouse and the calibration windows; this app is only the control panel.
class TrackerClient extends ChangeNotifier {
  TrackerClient._();
  static final TrackerClient instance = TrackerClient._();

  static String baseUrl = 'http://127.0.0.1:8001'; // only tests change it (fake tracker on another port)
  static const Duration _fast = Duration(milliseconds: 150);
  static const Duration _slow = Duration(milliseconds: 1000);

  final http.Client _http = http.Client();
  TrackerStatus? status;
  bool online = false;
  Timer? _timer;
  int _fastUsers = 0;
  bool _polling = false;
  String _lastBody = '';
  int _lastEventSeq = -1;
  bool _wantSpacePaused = false;
  bool? _pendingSpacebarClick; // a Spacebar change the tracker has not confirmed yet (e.g. made while it was off)

  final StreamController<String> _messages = StreamController<String>.broadcast();

  /// Results of finished tracker actions ("Calibration complete …", "Re-centred …") and command errors.
  Stream<String> get messages => _messages.stream;

  void start() {
    if (_timer != null) return;
    _poll();
    _reschedule();
  }

  /// Stops polling. Only for tests (the app polls for its whole life).
  @visibleForTesting
  void stopPolling() {
    _timer?.cancel();
    _timer = null;
  }

  /// Screens that show live values call useFastPolling(true) in initState and (false) in dispose.
  void useFastPolling(bool on) {
    _fastUsers = (_fastUsers + (on ? 1 : -1)).clamp(0, 1000);
    _reschedule();
  }

  void _reschedule() {
    _timer?.cancel();
    _timer = Timer.periodic(_fastUsers > 0 ? _fast : _slow, (_) => _poll());
  }

  Future<void> refresh() => _poll();

  Future<void> _poll() async {
    if (_polling) return;
    _polling = true;
    final wasOnline = online;
    try {
      final r = await _http.get(Uri.parse('$baseUrl/status')).timeout(const Duration(milliseconds: 1500));
      if (r.statusCode != 200) throw Exception('status ${r.statusCode}');
      online = true;
      if (r.body != _lastBody) {
        _lastBody = r.body;
        status = TrackerStatus(jsonDecode(r.body) as Map<String, dynamic>);
        final seq = status!.eventSeq;
        if (_lastEventSeq >= 0 && seq > _lastEventSeq && status!.eventMessage.isNotEmpty) {
          _messages.add(status!.eventMessage);
        }
        _lastEventSeq = seq;
        notifyListeners();
      }
      if (!wasOnline) {
        // a (re)started tracker does not know who owns the Spacebar yet
        unawaited(_sendSpacePause());
        final pending = _pendingSpacebarClick;
        if (pending != null) unawaited(setSpacebarClick(pending));
      }
    } catch (_) {
      online = false;
      _lastEventSeq = -1;
      if (wasOnline || status != null) {
        status = null;
        _lastBody = '';
        notifyListeners();
      }
    } finally {
      _polling = false;
    }
  }

  /// Sends a command; returns true on success, otherwise posts the tracker's reason to [messages].
  Future<bool> command(String path, [Map<String, dynamic>? body, bool bringsWindow = false]) async {
    if (bringsWindow) WinNative.allowOtherAppToFront();
    try {
      final r = await _http
          .post(Uri.parse('$baseUrl$path'),
              headers: {'Content-Type': 'application/json'}, body: jsonEncode(body ?? const {}))
          .timeout(const Duration(seconds: 20));
      final data = jsonDecode(r.body) as Map<String, dynamic>;
      if (r.statusCode == 200 && data['ok'] == true) {
        unawaited(_poll());
        return true;
      }
      _messages.add(data['error']?.toString() ?? 'Eye tracker error ${r.statusCode}');
    } catch (_) {
      _messages.add('Eye tracker is not running');
    }
    return false;
  }

  // ── convenience ─────────────────────────────────────────────────
  Future<bool> setEyeEnabled(String eye, bool on) => command('/eyes/$eye/enabled', {'enabled': on});
  Future<bool> setCamera(String eye, int id) => command('/eyes/$eye/camera', {'camera_id': id});
  Future<bool> setFlip(String eye, bool h, bool v) => command('/eyes/$eye/flip', {'h': h, 'v': v});
  Future<bool> setAlgorithm(String id) => command('/algorithm', {'name': id});
  Future<bool> setPupilSmoothing(double level) => command('/pupil_smoothing', {'level': level});
  Future<bool> setMouseEnabled(bool on) => command('/mouse/enabled', {'enabled': on});
  Future<bool> setMouseMode(String mode) => command('/mouse/mode', {'mode': mode});
  Future<bool> setMouseParams(Map<String, dynamic> p) => command('/mouse/params', p);
  Future<bool> resetMouse() => command('/mouse/reset');
  Future<bool> startCalibration() => command('/calibration/start', null, true);
  Future<bool> startFineTune() => command('/calibration/finetune', null, true);
  Future<bool> startRecentre() => command('/calibration/recentre', null, true);
  Future<bool> setRecordCalibration(bool on) => command('/calibration/record', {'enabled': on});
  Future<bool> setShowGazePointer(bool on) => command('/calibration/pointer', {'enabled': on});
  Future<bool> setAutoRecentre(bool on) => command('/calibration/auto_recentre', {'enabled': on});

  /// Auto re-centre: one button selection's (centre - cursor) in screen px. Quiet: no messages on failure.
  Future<void> reportSelectionOffset(double? dx, double? dy, int ageMs) async {
    try {
      await _http
          .post(Uri.parse('$baseUrl/calibration/implicit'),
              headers: {'Content-Type': 'application/json'}, body: jsonEncode({'dx': dx, 'dy': dy, 'age_ms': ageMs}))
          .timeout(const Duration(seconds: 2));
    } catch (_) {}
  }
  Future<bool> openChart() => command('/calibration/chart/open', null, true);
  Future<bool> openEyeView() => command('/eyeview/open', null, true);

  /// ONE Spacebar setting for the app and the tracker: Settings "Spacebar Select" and Tracking "Spacebar Click"
  /// are the same switch. The tracker's saved `mouse_spacebar_click` is the stored value; the app mirrors it.
  bool? get pendingSpacebarClick => _pendingSpacebarClick;
  Future<void> setSpacebarClick(bool on) async {
    _pendingSpacebarClick = on;
    notifyListeners();
    if (!online) return; // sent when the tracker comes up
    if (await command('/mouse/params', {'spacebar_click': on}) && _pendingSpacebarClick == on) {
      _pendingSpacebarClick = null;
      notifyListeners();
    }
  }

  /// Spacebar ownership: while this app is in front and its own Spacebar Select is on, the tracker's
  /// Spacebar click pauses (otherwise one press would select twice). Not saved in the tracker settings.
  Future<void> setAppOwnsSpacebar(bool owns) async {
    _wantSpacePaused = owns;
    await _sendSpacePause();
  }

  Future<void> _sendSpacePause() async {
    try {
      await _http
          .post(Uri.parse('$baseUrl/mouse/spacebar_pause'),
              headers: {'Content-Type': 'application/json'}, body: jsonEncode({'paused': _wantSpacePaused}))
          .timeout(const Duration(seconds: 2));
    } catch (_) {}
  }

  /// Graceful stop (mouse off, windows closed, cameras released).
  Future<void> shutdownService() async {
    try {
      await _http.post(Uri.parse('$baseUrl/shutdown'), body: '{}').timeout(const Duration(seconds: 2));
    } catch (_) {}
  }

  String chartUrl(num? version) => '$baseUrl/calibration/chart?v=${version ?? 0}';
}

/// Typed read-only view of GET /status.
class TrackerStatus {
  final Map<String, dynamic> raw;
  TrackerStatus(this.raw);

  Map<String, dynamic> _m(String k) => (raw[k] as Map?)?.cast<String, dynamic>() ?? const {};

  EyeStatus eye(String e) => EyeStatus((_m('eyes')[e] as Map?)?.cast<String, dynamic>() ?? const {});
  String get algorithm => raw['algorithm']?.toString() ?? 'ArcFit';
  List<Map<String, String>> get algorithms => ((raw['algorithms'] as List?) ?? const [])
      .map((a) => {'id': a['id'].toString(), 'label': a['label'].toString()})
      .toList();
  double get pupilSmoothing => (raw['pupil_smoothing'] as num?)?.toDouble() ?? 3;
  bool get recordCalibration => raw['record_calibration'] == true;
  bool get autoRecentre => raw['auto_recentre'] == true;
  int get autoRecentreCount => ((raw['auto_recentre_info'] as Map?)?['count'] as num?)?.toInt() ?? 0;
  List<double> get autoRecentreScale =>
      (((raw['auto_recentre_info'] as Map?)?['scale'] as List?) ?? const [1.0, 1.0]).map((e) => (e as num).toDouble()).toList();
  List<int> get autoRecentreShift =>
      (((raw['auto_recentre_info'] as Map?)?['shift'] as List?) ?? const [0, 0]).map((e) => (e as num).toInt()).toList();
  bool get showGazePointer => raw['show_gaze_pointer'] != false;   // older tracker without the key: on
  String? get activity => raw['activity'] as String?;
  bool get eyeViewOpen => raw['eye_view_open'] == true;
  int get eventSeq => (_m('event')['seq'] as num?)?.toInt() ?? 0;
  String get eventMessage => _m('event')['message']?.toString() ?? '';

  // mouse
  Map<String, dynamic> get _mouse => _m('mouse');
  bool get mouseEnabled => _mouse['enabled'] == true;
  String get mouseMode => _mouse['mode']?.toString() ?? 'smoother';
  String get cursorState => _mouse['state']?.toString() ?? '';
  List? get cursor => _mouse['cursor'] as List?;
  double get mouseFps => (_mouse['fps'] as num?)?.toDouble() ?? 0;
  String get signalMode => _mouse['signal_mode']?.toString() ?? 'No Signal';
  double get alpha => (_mouse['alpha'] as num?)?.toDouble() ?? 0.15;
  double get gain => (_mouse['gain'] as num?)?.toDouble() ?? 1.0;
  double get dpi => (_mouse['dpi'] as num?)?.toDouble() ?? 96;
  int get graceMs => (_mouse['grace_ms'] as num?)?.toInt() ?? 240;
  double get effectiveGain => (_mouse['effective_gain'] as num?)?.toDouble() ?? 1.0;
  bool get spacebarClick => _mouse['spacebar_click'] == true;
  bool get spacebarPaused => _mouse['spacebar_paused'] == true;
  bool get failsafeTripped => _mouse['failsafe_tripped'] == true;

  // calibration
  Map<String, dynamic> get _cal => _m('calibration');
  bool get calibrated => _cal['calibrated'] == true;
  String get grade => _cal['grade']?.toString() ?? 'N/A';
  String get gradeLetter => calibrated && grade.isNotEmpty ? grade[0] : '–';
  double? get meanErrorPx => (_cal['mean_error_px'] as num?)?.toDouble();
  double? get pursuitRmse => (_cal['pursuit_rmse'] as num?)?.toDouble();
  double? get meanJitter => (_cal['mean_jitter'] as num?)?.toDouble();
  String get calibratedAt => _cal['calibrated_at']?.toString() ?? '';
  bool get canFineTune => _cal['can_finetune'] == true;
  num? get chartVersion => _cal['chart_version'] as num?;
  List? get offset => _cal['offset'] as List?;
}

class EyeStatus {
  final Map<String, dynamic> raw;
  EyeStatus(this.raw);
  bool get enabled => raw['enabled'] != false;
  bool get connected => raw['connected'] == true;
  int get cameraId => (raw['camera_id'] as num?)?.toInt() ?? 0;
  double get fps => (raw['fps'] as num?)?.toDouble() ?? 0;
  double get detFps => (raw['det_fps'] as num?)?.toDouble() ?? 0;
  double get conf => (raw['conf'] as num?)?.toDouble() ?? 0;
  double get latencyMs => (raw['latency_ms'] as num?)?.toDouble() ?? 0;
  bool get flipH => raw['flip_h'] == true;
  bool get flipV => raw['flip_v'] == true;
  List? get pupil => raw['pupil'] as List?;
  bool get tracking => connected && pupil != null;
}
