import 'package:flutter/widgets.dart';

/// Level 2 points (2026-11-25): the keyboard screen tags the buttons Level 2 uses, and [collect] reads their centres
/// when Level 2 starts. The keyboard screen stays laid out under the Tracking screen (go_router push), so the
/// positions are the real ones on this screen. Centres are sent in physical pixels inside the app window; the
/// tracker adds the window position.
class GazeTargets {
  GazeTargets._();

  /// Order = the order the points are shown after the centre dot.
  static const order = ['Q', 'P', 'Z', '.', 'Space', 'G', 'Speak', 'Clear', 'word_first', 'word_last'];
  static const _labels = {'word_first': 'First word', 'word_last': 'Last word'};

  static final Map<String, GlobalKey> _keys = {};
  static final Map<int, GlobalKey> _sentences = {};

  static GlobalKey key(String name) => _keys.putIfAbsent(name, () => GlobalKey(debugLabel: 'gaze:$name'));
  static GlobalKey sentenceKey(int i) => _sentences.putIfAbsent(i, () => GlobalKey(debugLabel: 'gaze:sentence$i'));

  static Rect? _rect(GlobalKey k) {
    final ro = k.currentContext?.findRenderObject();
    if (ro is! RenderBox || !ro.attached || !ro.hasSize) return null;
    return ro.localToGlobal(Offset.zero) & ro.size;
  }

  /// [{name, x, y}] in physical px inside the app window; buttons that are not on screen are left out.
  static List<Map<String, dynamic>> collect() {
    final views = WidgetsBinding.instance.platformDispatcher.views;
    final dpr = views.isEmpty ? 1.0 : views.first.devicePixelRatio;
    final out = <Map<String, dynamic>>[];
    void add(String name, Rect r) =>
        out.add({'name': name, 'x': r.center.dx * dpr, 'y': r.center.dy * dpr});
    for (final n in order) {
      final r = _keys[n] == null ? null : _rect(_keys[n]!);
      if (r != null) add(_labels[n] ?? n, r);
    }
    // first and last sentence tile that are fully visible in the list
    final list = _keys['sentence_list'] == null ? null : _rect(_keys['sentence_list']!);
    if (list != null) {
      final visible = <Rect>[];
      for (final i in (_sentences.keys.toList()..sort())) {
        final r = _rect(_sentences[i]!);
        if (r != null && r.top >= list.top - 1 && r.bottom <= list.bottom + 1) visible.add(r);
      }
      if (visible.isNotEmpty) add('First sentence', visible.first);
      if (visible.length > 1) add('Last sentence', visible.last);
    }
    return out;
  }
}
