// The menu screens (Settings, Eye Tracking tabs, System Status) build without layout errors at common window sizes.
// Uses a real /status reply of the tracker (test/fixtures/tracker_status.json). No network, no background programs.
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:synaptalk/core/services/tracker_client.dart';
import 'package:synaptalk/core/widgets/ui_kit.dart';
import 'package:synaptalk/features/dashboard/dashboard_screen.dart';
import 'package:synaptalk/features/settings/settings_screen.dart';
import 'package:synaptalk/features/tracking/tracking_screen.dart';

/// Real text widths instead of the test font (every letter a full square): Windows' Segoe UI is loaded under the
/// names google_fonts uses for Inter (similar width), and as the default font.
Future<void> loadRealFonts() async {
  Future<void> load(String family, String file) async {
    final bytes = File('C:/Windows/Fonts/$file').readAsBytesSync();
    await (FontLoader(family)..addFont(Future.value(ByteData.view(bytes.buffer)))).load();
  }
  for (final f in ['Inter_regular', 'Inter_500', 'Roboto']) {
    await load(f, 'segoeui.ttf');
  }
  for (final f in ['Inter_600', 'Inter_700', 'Inter_800']) {
    await load(f, 'segoeuib.ttf');
  }
}

void main() {
  setUpAll(loadRealFonts);
  final status = jsonDecode(File('test/fixtures/tracker_status.json').readAsStringSync()) as Map<String, dynamic>;
  final screens = <String, Widget Function()>{
    'Settings': () => const SettingsScreen(),
    'Eye Tracking: Sensors': () => const TrackingScreen(initialTab: 0),
    'Eye Tracking: Calibration': () => const TrackingScreen(initialTab: 1),
    'Eye Tracking: Mouse': () => const TrackingScreen(initialTab: 2),
    'System Status': () => const DashboardScreen(),
  };
  for (final size in const [Size(1920, 1080), Size(1366, 768), Size(1280, 720)]) {
    for (final e in screens.entries) {
      testWidgets('${e.key} at ${size.width.toInt()}x${size.height.toInt()}', (t) async {
        SharedPreferences.setMockInitialValues({});
        TrackerClient.instance.status = TrackerStatus(status);
        TrackerClient.instance.online = true;
        t.view.physicalSize = size;
        t.view.devicePixelRatio = 1.0;
        addTearDown(t.view.reset);
        await t.pumpWidget(MaterialApp(home: e.value()));
        await t.pump(const Duration(milliseconds: 300));
        expect(t.takeException(), isNull, reason: 'layout error on ${e.key}');
        // the menu frame is there and never wider than the screen
        final page = find.byType(MenuPage);
        expect(page, findsOneWidget);
        expect(t.getSize(page).width, lessThanOrEqualTo(size.width));
        await t.pumpWidget(const SizedBox());
        TrackerClient.instance.stopPolling(); // the Eye Tracking screen starts the app's status polling
        await t.pump(const Duration(seconds: 4)); // let pending HTTP timeouts of the screens finish
      });
    }
  }

  testWidgets('System Status: status badges sit at the right edge of their cards', (t) async {
    TrackerClient.instance.status = TrackerStatus(status);
    t.view.physicalSize = const Size(1920, 1080);
    t.view.devicePixelRatio = 1.0;
    addTearDown(t.view.reset);
    await t.pumpWidget(const MaterialApp(home: DashboardScreen()));
    await t.pump(const Duration(milliseconds: 300));
    final badges = find.descendant(of: find.byType(Wrap), matching: find.byType(StatusPill));
    expect(badges, findsNWidgets(6));
    for (final b in badges.evaluate()) {
      final card = find.ancestor(of: find.byWidget(b.widget), matching: find.byType(SizedBox)).first;
      final gap = t.getRect(card).right - t.getRect(find.byWidget(b.widget)).right;
      expect(gap, inInclusiveRange(17, 20), reason: 'badge should end at the card padding (18 px), gap $gap');
    }
    await t.pumpWidget(const SizedBox());
    TrackerClient.instance.stopPolling();
    await t.pump(const Duration(seconds: 4));
  });
}
