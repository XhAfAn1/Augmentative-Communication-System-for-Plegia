import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'tracker_client.dart';
import 'win_native.dart';

/// State of one background program.
enum ServiceState { notStarted, starting, running, external, failed, disabled }

/// One background program (eye tracker or AI backend) that the app starts, watches and stops.
class ManagedService {
  final String name; // 'tracker' | 'backend'
  final String label;
  final String healthUrl;
  final String marker; // text only OUR program's health answer contains (another program may use the port)
  final Duration startTimeout;
  ServiceState state = ServiceState.notStarted;
  String detail = '';
  Process? process;
  final List<DateTime> _restarts = [];

  ManagedService(this.name, this.label, this.healthUrl, this.marker, this.startTimeout);

  bool get up => state == ServiceState.running || state == ServiceState.external;
}

/// Starts the AI backend (backend/main.py, port 8000) and the eye tracker (tracker_service.py, port 8001) with
/// the app, restarts them if they crash, and stops them when the app closes.
///  * A program that is already running (started by hand) is used as it is and left running at exit.
///  * Started programs are put in a Windows job: if the app crashes, Windows ends them too.
///  * Output goes to Combined System/logs/<name>.log.
///  * Paths come from Combined System/system_config.json.
class SystemProcesses extends ChangeNotifier {
  SystemProcesses._();
  static final SystemProcesses instance = SystemProcesses._();

  final ManagedService tracker =
      ManagedService('tracker', 'Eye Tracker', '${TrackerClient.baseUrl}/health', '"eye-tracker"', const Duration(seconds: 30));
  final ManagedService backend =
      ManagedService('backend', 'AI & Speech Backend', 'http://127.0.0.1:8000/openapi.json', '"Hybrid Keyboard API"',
          const Duration(minutes: 4));

  String? rootDir;
  Map<String, dynamic> _cfg = {};
  bool _stopping = false;
  bool _started = false;

  List<ManagedService> get all => [backend, tracker];

  Future<void> startAll() async {
    if (_started) return;
    _started = true;
    rootDir = _findRoot();
    if (rootDir == null) {
      for (final s in all) {
        s.state = ServiceState.failed;
        s.detail = 'Combined System folder not found (needs "backend" and "Binocular Eye Tracking Module")';
      }
      notifyListeners();
      return;
    }
    _cfg = _readConfig();
    await Future.wait([_ensure(backend), _ensure(tracker)]);
  }

  Future<void> restart(ManagedService s) async {
    if (s.state == ServiceState.external) return;
    await _stop(s);
    s._restarts.clear();
    await _ensure(s);
  }

  /// Called when the app closes: tracker gracefully (mouse off, cameras released), backend ended.
  Future<void> stopAll() async {
    _stopping = true;
    await TrackerClient.instance.setAppOwnsSpacebar(false); // an external tracker keeps working normally
    await Future.wait([_stop(tracker), _stop(backend)]);
  }

  // ── internals ─────────────────────────────────────────────────────
  String? _findRoot() {
    final env = Platform.environment['AAC_SYSTEM_ROOT'];
    final starts = [if (env != null) env, Directory.current.path, File(Platform.resolvedExecutable).parent.path];
    for (final start in starts) {
      var d = Directory(start).absolute;
      for (var i = 0; i < 10; i++) {
        if (Directory(_join(d.path, 'backend')).existsSync() &&
            File(_join(d.path, 'Binocular Eye Tracking Module', 'tracker_service.py')).existsSync()) {
          return d.path;
        }
        final up = d.parent;
        if (up.path == d.path) break;
        d = up;
      }
    }
    return null;
  }

  Map<String, dynamic> _readConfig() {
    try {
      return jsonDecode(File(_join(rootDir!, 'system_config.json')).readAsStringSync()) as Map<String, dynamic>;
    } catch (_) {
      return {};
    }
  }

  String _python(String key, String fallback) {
    final v = (_cfg[key] as String?) ?? fallback;
    if (!v.contains('/') && !v.contains('\\')) return v; // on PATH
    final p = File(v).isAbsolute ? v : _join(rootDir!, v);
    return File(p).existsSync() ? p : 'python';
  }

  /// true = our program answers; false = nothing answers; null = ANOTHER program answers on that port.
  Future<bool?> _healthy(ManagedService s) async {
    try {
      final r = await http.get(Uri.parse(s.healthUrl)).timeout(const Duration(milliseconds: 1500));
      return r.body.contains(s.marker) ? true : null;
    } catch (_) {
      return false;
    }
  }

  void _set(ManagedService s, ServiceState st, String detail) {
    s.state = st;
    s.detail = detail;
    notifyListeners();
  }

  Future<void> _ensure(ManagedService s) async {
    if (_cfg['start_${s.name}'] == false) {
      _set(s, ServiceState.disabled, 'Start disabled in system_config.json');
      return;
    }
    final found = await _healthy(s);
    if (found == true) {
      _set(s, ServiceState.external, 'Already running (started outside the app)');
      return;
    }
    if (found == null) {
      _set(s, ServiceState.failed, 'Port ${Uri.parse(s.healthUrl).port} is used by another program (not the ${s.label})');
      return;
    }
    final isTracker = s.name == 'tracker';
    final exe = isTracker ? _python('tracker_python', 'python') : _python('backend_python', 'python');
    final dir = isTracker ? _join(rootDir!, 'Binocular Eye Tracking Module') : _join(rootDir!, 'backend');
    final args = isTracker ? ['-u', 'tracker_service.py', '--parent-pid', '$pid'] : ['-u', 'main.py'];
    _set(s, ServiceState.starting, 'Starting…');
    final Process p;
    try {
      p = await Process.start(exe, args,
          workingDirectory: dir, environment: {'PYTHONUNBUFFERED': '1', 'PYTHONIOENCODING': 'utf-8'});
    } catch (e) {
      _set(s, ServiceState.failed, 'Could not start "$exe": $e');
      return;
    }
    s.process = p;
    WinNative.endWithThisApp(p.pid);
    _pipeToLog(s, p);
    unawaited(p.exitCode.then((code) => _onExit(s, p, code)));

    final deadline = DateTime.now().add(s.startTimeout);
    while (DateTime.now().isBefore(deadline)) {
      if (s.process != p) return; // exited / restarted meanwhile
      if (await _healthy(s) == true) {
        _set(s, ServiceState.running, 'Running (pid ${p.pid})');
        if (isTracker) unawaited(TrackerClient.instance.refresh());
        return;
      }
      await Future.delayed(const Duration(milliseconds: 500));
    }
    if (s.process == p) _set(s, ServiceState.failed, 'Did not answer within ${s.startTimeout.inSeconds} s (see logs)');
  }

  void _onExit(ManagedService s, Process p, int code) {
    if (s.process != p) return;
    s.process = null;
    if (_stopping) {
      _set(s, ServiceState.notStarted, 'Stopped');
      return;
    }
    if (s.name == 'tracker' && code == 3) {
      _set(s, ServiceState.failed, 'Port 8001 busy: another eye tracker (launcher.py?) is running');
      return;
    }
    // crash: restart, at most 3 times per minute
    final now = DateTime.now();
    s._restarts.removeWhere((t) => now.difference(t) > const Duration(minutes: 1));
    if (s._restarts.length >= 3) {
      _set(s, ServiceState.failed, 'Stopped unexpectedly (exit $code) 3 times; see logs/${s.name}.log');
      return;
    }
    s._restarts.add(now);
    _set(s, ServiceState.starting, 'Stopped unexpectedly (exit $code), restarting…');
    Future.delayed(const Duration(seconds: 2), () {
      if (!_stopping) _ensure(s);
    });
  }

  Future<void> _stop(ManagedService s) async {
    final p = s.process;
    if (p == null) return;
    s.process = null; // so its exit is not taken for a crash
    _set(s, ServiceState.notStarted, 'Stopping…');
    if (s.name == 'tracker') {
      await TrackerClient.instance.shutdownService();
      try {
        await p.exitCode.timeout(const Duration(seconds: 5));
        return;
      } catch (_) {}
    }
    p.kill();
    try {
      await p.exitCode.timeout(const Duration(seconds: 3));
    } catch (_) {}
  }

  void _pipeToLog(ManagedService s, Process p) {
    IOSink? sink;
    try {
      final dir = Directory(_join(rootDir!, 'logs'))..createSync(recursive: true);
      final f = File(_join(dir.path, '${s.name}.log'));
      if (f.existsSync() && f.lengthSync() > 5 * 1024 * 1024) f.renameSync('${f.path}.old');
      sink = f.openWrite(mode: FileMode.append)..writeln('\n===== ${DateTime.now()} start pid ${p.pid} =====');
    } catch (_) {}
    void write(List<int> data) {
      try {
        sink?.add(data);
      } catch (_) {}
    }
    p.stdout.listen(write, onDone: () {});
    p.stderr.listen(write, onDone: () async {
      try {
        await sink?.flush();
        await sink?.close();
      } catch (_) {}
    });
  }

  static String _join(String a, [String? b, String? c]) =>
      [a, if (b != null) b, if (c != null) c].join(Platform.pathSeparator);
}
