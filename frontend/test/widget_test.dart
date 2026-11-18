// Smoke test: the keyboard screen builds and shows its main parts. It does not start the whole app
// (SynapTalkApp starts the real tracker and backend programs).
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:synaptalk/features/keyboard/gaze_keyboard.dart';
import 'package:synaptalk/features/keyboard/keyboard_screen.dart';

void main() {
  testWidgets('keyboard screen builds with output box and keys', (tester) async {
    SharedPreferences.setMockInitialValues({});
    tester.view.physicalSize = const Size(1920, 1080);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(const MaterialApp(home: KeyboardScreen()));
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.text('Output'), findsOneWidget);
    expect(find.byType(KeyboardKey), findsWidgets);
    await tester.pumpWidget(const SizedBox());
  });
}
