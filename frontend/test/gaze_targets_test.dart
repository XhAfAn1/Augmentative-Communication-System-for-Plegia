// Level 2 points (2026-11-25): the keyboard screen tags its buttons and GazeTargets.collect() returns their centres
// in physical pixels; checked at 100 % and 150 % scaling, and with the Tracking-like screen pushed on top.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:synaptalk/core/services/gaze_targets.dart';
import 'package:synaptalk/features/keyboard/keyboard_screen.dart';

void main() {
  for (final dpr in [1.0, 1.5]) {
    testWidgets('keyboard buttons give Level 2 points (scale $dpr)', (tester) async {
      SharedPreferences.setMockInitialValues({});
      tester.view.physicalSize = const Size(1920, 1080);
      tester.view.devicePixelRatio = dpr;
      addTearDown(tester.view.reset);
      final nav = GlobalKey<NavigatorState>();
      await tester.pumpWidget(MaterialApp(navigatorKey: nav, home: const KeyboardScreen()));
      await tester.pump(const Duration(milliseconds: 100));

      // another screen on top (like Tracking): the keyboard stays laid out underneath
      nav.currentState!.push(MaterialPageRoute(builder: (_) => const Scaffold(body: Text('Tracking'))));
      await tester.pumpAndSettle(const Duration(milliseconds: 100));

      final pts = GazeTargets.collect();
      final names = pts.map((p) => p['name']).toList();
      // ignore: avoid_print
      print('dpr $dpr: ${pts.map((p) => '${p['name']} (${(p['x'] as double).round()}, ${(p['y'] as double).round()})').join(', ')}');
      for (final n in ['Q', 'P', 'Z', '.', 'Space', 'G', 'Speak', 'Clear', 'First word', 'Last word', 'First sentence', 'Last sentence']) {
        expect(names, contains(n));
      }
      expect(pts.length, 12);
      for (final p in pts) {
        expect(p['x'] as double, inInclusiveRange(0, 1920));
        expect(p['y'] as double, inInclusiveRange(0, 1080));
      }
      final q = pts.firstWhere((p) => p['name'] == 'Q'), pp = pts.firstWhere((p) => p['name'] == 'P');
      final z = pts.firstWhere((p) => p['name'] == 'Z');
      expect(q['x'] as double, lessThan(pp['x'] as double));     // Q left of P
      expect(q['y'] as double, lessThan(z['y'] as double));      // Q row above Z row
      await tester.pumpWidget(const SizedBox());
    });
  }
}
