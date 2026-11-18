// The selection click must never slow a selection down (it plays asynchronously from memory).
import 'package:flutter_test/flutter_test.dart';
import 'package:synaptalk/core/services/win_native.dart';

void main() {
  test('selection click returns at once', () {
    expect(WinNative.playClick(), isTrue, reason: 'Windows refused to play the click'); // builds the sound
    final sw = Stopwatch()..start();
    for (var i = 0; i < 20; i++) {
      WinNative.playClick();
    }
    sw.stop();
    expect(sw.elapsedMicroseconds / 20, lessThan(5000), reason: 'per click ${sw.elapsedMicroseconds / 20} us');
    print('per click: ${(sw.elapsedMicroseconds / 20).toStringAsFixed(0)} us');
  });
}
