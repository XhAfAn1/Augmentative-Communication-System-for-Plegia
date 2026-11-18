// Auto re-centre: a dwell selection reports (button centre - where the cursor rested) to the tracker 1.5 s later;
// nothing when switched off; Backspace cancels the report of the selection before it; wide keys: vertical only.
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:synaptalk/core/services/gaze_offset_learner.dart';
import 'package:synaptalk/core/services/tracker_client.dart';
import 'package:synaptalk/core/widgets/dwell_wrapper.dart' show dwellDurationMs;
import 'package:synaptalk/features/keyboard/gaze_keyboard.dart';

void main() {
  final sent = <List<double?>>[];
  setUp(() {
    sent.clear();
    final b = TestWidgetsFlutterBinding.ensureInitialized();
    b.platformDispatcher.views.first.physicalSize = const Size(1200, 600);
    b.platformDispatcher.views.first.devicePixelRatio = 1.0;
    GazeOffsetLearner.instance.send = (dx, dy, ageMs) {
      sent.add([dx, dy]);
      expect(ageMs, greaterThanOrEqualTo(0)); // real elapsed time (fake test clock does not move it)
    };
    flutterDwellEnabled.value = true;
    selectionSoundEnabled.value = false;
    dwellDurationMs.value = 1000;
  });

  Future<TestGesture> rest(WidgetTester t, Finder key, Offset fromCentre) async {
    final g = await t.createGesture(kind: PointerDeviceKind.mouse);
    await g.addPointer(location: const Offset(1000, 500)); // outside all keys
    await g.moveTo(t.getCenter(key) + fromCentre);
    for (var i = 0; i < 5; i++) {
      await g.moveTo(t.getCenter(key) + fromCentre); // the cursor rests here (hover samples)
      await t.pump(const Duration(milliseconds: 50));
    }
    return g;
  }

  Widget keys() => MaterialApp(
      home: Scaffold(
          body: Align(
              alignment: Alignment.topLeft,
              child: Row(mainAxisSize: MainAxisSize.min, children: [
        for (final l in ['a', 'bs', 'space'])
          SizedBox(
              width: l == 'space' ? 600 : 140,
              height: 150,
              child: KeyboardKey(
                  label: l,
                  keyType: l == 'bs' ? KeyType.backspace : (l == 'space' ? KeyType.space : KeyType.letter),
                  onPressed: () {})),
      ]))));
  Finder k(String l) => find.byWidgetPredicate((w) => w is KeyboardKey && w.label == l);

  testWidgets('dwell selection reports the offset 1.5 s later', (t) async {
    TrackerClient.instance.status = TrackerStatus({'auto_recentre': true});
    await t.pumpWidget(keys());
    final g = await rest(t, k('a'), const Offset(0, 30)); // cursor rests 30 px BELOW the key centre
    await t.pump(const Duration(milliseconds: 1100)); // dwell completes
    expect(sent, isEmpty); // not yet: waits 1.5 s for a possible Backspace
    await t.pump(const Duration(milliseconds: 1600));
    expect(sent, hasLength(1));
    expect(sent[0][0], closeTo(0, 0.5));
    expect(sent[0][1], closeTo(-30, 0.5)); // centre - cursor: the correction moves the cursor up
    await t.pump(const Duration(milliseconds: 300)); // let the key's 120 ms press flash finish
    await g.removePointer();
  });

  testWidgets('switched off: nothing is sent', (t) async {
    TrackerClient.instance.status = TrackerStatus({'auto_recentre': false});
    await t.pumpWidget(keys());
    final g = await rest(t, k('a'), const Offset(0, 30));
    await t.pump(const Duration(milliseconds: 3000));
    expect(sent, isEmpty);
    await t.pump(const Duration(milliseconds: 300)); // let the key's 120 ms press flash finish
    await g.removePointer();
  });

  testWidgets('Backspace cancels the selection before it, and wide keys teach vertical only', (t) async {
    TrackerClient.instance.status = TrackerStatus({'auto_recentre': true});
    await t.pumpWidget(keys());
    final g = await rest(t, k('a'), const Offset(0, 30));
    await t.pump(const Duration(milliseconds: 1100)); // 'a' selected (report waiting)
    await g.moveTo(t.getCenter(k('bs')) + const Offset(10, 0));
    for (var i = 0; i < 5; i++) {
      await g.moveTo(t.getCenter(k('bs')) + const Offset(10, 0));
      await t.pump(const Duration(milliseconds: 50));
    }
    await t.pump(const Duration(milliseconds: 1100)); // Backspace selected -> 'a' was a mistake
    await t.pump(const Duration(milliseconds: 1600));
    expect(sent, hasLength(1)); // only Backspace's own report
    expect(sent[0][0], closeTo(-10, 0.5));
    sent.clear();
    await g.moveTo(t.getCenter(k('space')) + const Offset(200, -20));
    for (var i = 0; i < 5; i++) {
      await g.moveTo(t.getCenter(k('space')) + const Offset(200, -20));
      await t.pump(const Duration(milliseconds: 50));
    }
    await t.pump(const Duration(milliseconds: 1100)); // dwell completes
    await t.pump(const Duration(milliseconds: 1600)); // report sent
    expect(sent, hasLength(1));
    expect(sent[0][0], isNull); // Space is 600 px wide: you don't look at its horizontal centre
    expect(sent[0][1], closeTo(20, 0.5));
    await t.pump(const Duration(milliseconds: 300)); // let the key's 120 ms press flash finish
    await g.removePointer();
    await t.pumpWidget(const SizedBox());
  });

  test('offset maths: screen pixels and long-axis rule', () {
    final d = GazeOffsetLearner.delta(const Offset(100, 100), const Offset(90, 130), const Size(140, 150), 1.5);
    expect(d.dx, closeTo(15, 0.01)); // (100-90) * 1.5 (Windows scaling 150 %)
    expect(d.dy, closeTo(-45, 0.01));
    final wide = GazeOffsetLearner.delta(const Offset(0, 0), const Offset(5, 5), const Size(330, 60), 1.0);
    expect(wide.dx, isNull);
    expect(wide.dy, closeTo(-5, 0.01));
  });
}
