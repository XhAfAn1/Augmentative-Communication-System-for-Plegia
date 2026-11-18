// Process-manager test against a FAKE Combined System folder (env AAC_SYSTEM_ROOT): tiny Python servers stand in
// for the backend (8000) and the tracker (8001). Never starts the real tracker or backend.
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:synaptalk/core/services/system_processes.dart';


/// true if nothing listens on these ports (the real AAC app / tracker / backend is not running)
Future<bool> portsFree(List<int> ports) async {
  for (final p in ports) {
    try {
      await (await ServerSocket.bind(InternetAddress.loopbackIPv4, p)).close();
    } on SocketException {
      return false;
    }
  }
  return true;
}

void main() {
  final root = Platform.environment['AAC_SYSTEM_ROOT'];
  test('start, crash-restart, stop', () async {
    if (!await portsFree([8000, 8001])) {
      markTestSkipped('the real backend / tracker is running (ports 8000/8001): close the AAC app first');
      return;
    }
    final sp = SystemProcesses.instance;
    await sp.startAll();
    expect(sp.tracker.state, ServiceState.running, reason: sp.tracker.detail);
    expect(sp.backend.state, ServiceState.running, reason: sp.backend.detail);
    final firstPid = sp.tracker.process!.pid;
    // simulated crash -> automatic restart
    File('$root/crash_8001.flag').writeAsStringSync('x');
    final until = DateTime.now().add(const Duration(seconds: 20));
    while (DateTime.now().isBefore(until) &&
        !(sp.tracker.state == ServiceState.running && sp.tracker.process?.pid != firstPid)) {
      await Future.delayed(const Duration(milliseconds: 200));
    }
    expect(sp.tracker.state, ServiceState.running, reason: sp.tracker.detail);
    expect(sp.tracker.process!.pid, isNot(firstPid));
    final trackerPid = sp.tracker.process!.pid, backendPid = sp.backend.process!.pid;
    // log file
    expect(File('$root/logs/tracker.log').readAsStringSync(), contains('simulated crash'));
    await sp.stopAll();
    expect(File('$root/logs/tracker.log').readAsStringSync(), contains('graceful shutdown'));
    final r = await Process.run('tasklist', ['/FI', 'PID eq $trackerPid']);
    final r2 = await Process.run('tasklist', ['/FI', 'PID eq $backendPid']);
    expect(r.stdout.toString().contains('python'), isFalse, reason: 'tracker still running');
    expect(r2.stdout.toString().contains('python'), isFalse, reason: 'backend still running');
  }, timeout: const Timeout(Duration(seconds: 90)), skip: root == null ? 'set AAC_SYSTEM_ROOT to a fake root' : false);
}
