// Keyboard keys show a hover look (blue border + tint) while the cursor is on them, with dwell on AND off,
// like the other buttons.
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:synaptalk/core/theme/app_colors.dart';
import 'package:synaptalk/core/widgets/dwell_wrapper.dart' show dwellDurationMs;
import 'package:synaptalk/features/keyboard/gaze_keyboard.dart';

BoxDecoration _deco(WidgetTester t) => t
    .widget<AnimatedContainer>(find.descendant(of: find.byType(KeyboardKey), matching: find.byType(AnimatedContainer)))
    .decoration! as BoxDecoration;

void main() {
  for (final dwell in [true, false]) {
    testWidgets('key hover look with dwell ${dwell ? 'on' : 'off'}', (t) async {
      flutterDwellEnabled.value = dwell;
      await t.pumpWidget(const MaterialApp(
          home: Scaffold(body: Center(child: SizedBox(width: 80, height: 60, child: KeyboardKey(label: 'a'))))));
      expect((_deco(t).border as Border).top.color, Colors.transparent);

      final mouse = await t.createGesture(kind: PointerDeviceKind.mouse);
      await mouse.addPointer(location: const Offset(1, 1));
      await mouse.moveTo(t.getCenter(find.byType(KeyboardKey)));
      await t.pump(const Duration(milliseconds: 100));
      expect((_deco(t).border as Border).top.color, AppColors.primaryBlue);
      expect(_deco(t).color, isNot(AppColors.keyBg));

      await mouse.moveTo(const Offset(1, 1));
      await t.pump(const Duration(milliseconds: 100));
      expect((_deco(t).border as Border).top.color, Colors.transparent);
      await mouse.removePointer();
      await t.pumpWidget(const SizedBox()); // dispose the key (stops its dwell timer)
    });
  }

  // the keys follow the Settings Dwell Time slider (they used a fixed built-in time before)
  for (final ms in [600, 2000]) {
    testWidgets('key dwell fires after the slider time ($ms ms)', (t) async {
      flutterDwellEnabled.value = true;
      dwellDurationMs.value = ms;
      var presses = 0;
      await t.pumpWidget(MaterialApp(
          home: Scaffold(
              body: Center(
                  child: SizedBox(width: 80, height: 60, child: KeyboardKey(label: 'a', onPressed: () => presses++))))));
      final mouse = await t.createGesture(kind: PointerDeviceKind.mouse);
      await mouse.addPointer(location: const Offset(1, 1));
      await mouse.moveTo(t.getCenter(find.byType(KeyboardKey)));
      await t.pump(); // first frame: the dwell animation's clock starts here
      await t.pump(Duration(milliseconds: ms - 100));
      expect(presses, 0);
      await t.pump(const Duration(milliseconds: 200));
      expect(presses, 1);
      await mouse.moveTo(const Offset(1, 1));
      await mouse.removePointer();
      await t.pumpWidget(const SizedBox());
      await t.pump(const Duration(milliseconds: 200)); // pressed-flash timer
    });
  }
}
