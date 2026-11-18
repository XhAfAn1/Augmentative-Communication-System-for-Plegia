// One Spacebar setting: Settings "Spacebar Select" and Tracking "Spacebar Click" are the same switch.
// A fake tracker on 127.0.0.1:8011 (not the real tracker's port) checks that the client sends a change,
// keeps a change made while the tracker is off and sends it when the tracker comes up, and follows a change
// made on the tracker side.
import 'dart:convert';
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:synaptalk/core/services/tracker_client.dart';

void main() {
  test('Spacebar switches stay in sync with the tracker setting', () async {
    final c = TrackerClient.instance;
    var spacebar = true;
    final posts = <bool>[];

    TrackerClient.baseUrl = 'http://127.0.0.1:8011'; // never the real tracker (it would change the saved setting)

    // tracker off: the change waits
    await c.refresh();
    expect(c.online, isFalse);
    await c.setSpacebarClick(false);
    expect(c.pendingSpacebarClick, isFalse);

    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 8011);
    server.listen((req) async {
      final body = await utf8.decoder.bind(req).join();
      if (req.uri.path == '/mouse/params') {
        spacebar = (jsonDecode(body) as Map)['spacebar_click'] as bool;
        posts.add(spacebar);
      }
      req.response.headers.contentType = ContentType.json;
      req.response.write(jsonEncode({
        'ok': true,
        'mouse': {'spacebar_click': spacebar, 'spacebar_paused': false},
      }));
      await req.response.close();
    });

    // tracker comes up with the old value: the waiting change is sent
    await c.refresh();
    await Future<void>.delayed(const Duration(milliseconds: 300));
    expect(posts, [false]);
    expect(c.pendingSpacebarClick, isNull);
    expect(c.status!.spacebarClick, isFalse);

    // change while online
    await c.setSpacebarClick(true);
    expect(posts, [false, true]);
    expect(c.pendingSpacebarClick, isNull);

    // change on the tracker side (launcher / Reset to defaults): the client follows
    spacebar = false;
    await c.refresh();
    expect(c.status!.spacebarClick, isFalse);
    expect(c.pendingSpacebarClick, isNull);

    await server.close(force: true);
  });
}
