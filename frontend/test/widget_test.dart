import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:synaptalk/app.dart';
import 'package:synaptalk/features/keyboard/keyboard_provider.dart';
import 'package:synaptalk/features/settings/settings_provider.dart';

void main() {
  testWidgets('SynapTalk app starts and shows keyboard screen', (tester) async {
    await tester.pumpWidget(
      MultiProvider(
        providers: [
          ChangeNotifierProvider(create: (_) => KeyboardProvider()),
          ChangeNotifierProvider(create: (_) => SettingsProvider()),
        ],
        child: const SynapTalkApp(),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Output'), findsOneWidget);
  });
}
