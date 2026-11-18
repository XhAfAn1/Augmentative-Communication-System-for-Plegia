// A different program already answers on the backend's port 8000: the app must not take it for the backend.
// Runs against a FAKE Combined System folder (env AAC_SYSTEM_ROOT, same as system_processes_test.dart).
import 'dart:convert';
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
  test('foreign program on port 8000 is reported, not used', () async {
    if (!await portsFree([8000, 8001])) {
      markTestSkipped('the real backend / tracker is running (ports 8000/8001): close the AAC app first');
      return;
    }
    final foreign = await HttpServer.bind(InternetAddress.loopbackIPv4, 8000);
    foreign.listen((req) {
      req.response.write(jsonEncode({'hello': 'some other program'}));
      req.response.close();
    });
    final sp = SystemProcesses.instance;
    await sp.startAll();
    expect(sp.backend.state, ServiceState.failed, reason: sp.backend.detail);
    expect(sp.backend.detail, contains('another program'));
    expect(sp.tracker.state, ServiceState.running, reason: sp.tracker.detail);
    await sp.stopAll();
    await foreign.close(force: true);
  }, timeout: const Timeout(Duration(seconds: 60)), skip: root == null ? 'set AAC_SYSTEM_ROOT to a fake root' : false);
}
