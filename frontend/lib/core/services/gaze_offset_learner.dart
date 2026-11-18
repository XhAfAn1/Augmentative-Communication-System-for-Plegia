import 'dart:async';
import 'package:flutter/widgets.dart';
import 'tracker_client.dart';

/// Recent gaze-cursor positions over one button (global logical px), for "Auto re-centre while using".
class HoverTrail {
  final List<(int, Offset)> _pts = [];
  final Stopwatch _clock = Stopwatch()..start();

  void clear() => _pts.clear();

  void add(Offset p) {
    final now = _clock.elapsedMilliseconds;
    _pts.add((now, p));
    _pts.removeWhere((e) => now - e.$1 > 2000);
  }

  /// Mean position over the last [window] (where the cursor RESTED, not where it came from); null if none.
  Offset? mean([Duration window = const Duration(milliseconds: 600)]) {
    final from = _clock.elapsedMilliseconds - window.inMilliseconds;
    final r = _pts.where((e) => e.$1 >= from).map((e) => e.$2).toList();
    if (r.isEmpty) return null;
    return r.reduce((a, b) => a + b) / r.length.toDouble();
  }
}

/// Auto re-centre while using: when a button is selected by dwell or Spacebar, the user was looking at it, so
/// (button centre - where the cursor rested) is a measurement of the gaze error there. It is sent to the tracker
/// 1.5 s later (with its age, so the tracker can look up where the gaze rested), unless Backspace / Delete is
/// selected in between (then that selection was a mistake). The tracker fits a shift + scale correction to the
/// last 5 selections (gaze_engine.add_implicit_sample).
class GazeOffsetLearner {
  GazeOffsetLearner._();
  static final GazeOffsetLearner instance = GazeOffsetLearner._();

  static const Duration delay = Duration(milliseconds: 1500);
  static const double maxAxisSize = 200; // logical px: a longer button teaches nothing along that axis

  final List<Timer> _pending = [];

  /// Where reports go (the tracker); tests replace it.
  void Function(double? dx, double? dy, int ageMs) send = TrackerClient.instance.reportSelectionOffset;

  /// (centre - cursor) in SCREEN pixels; an axis is null when the button is too long along it
  /// (e.g. Space or a sentence: the eyes do not rest on its centre in that direction).
  static ({double? dx, double? dy}) delta(Offset centre, Offset cursor, Size size, double devicePixelRatio) {
    final d = (centre - cursor) * devicePixelRatio;
    return (dx: size.width <= maxAxisSize ? d.dx : null, dy: size.height <= maxAxisSize ? d.dy : null);
  }

  void selected(BuildContext context, HoverTrail trail) {
    if (TrackerClient.instance.status?.autoRecentre != true) return; // switched off: send nothing
    final box = context.findRenderObject();
    if (box is! RenderBox || !box.attached || !box.hasSize) return;
    final cursor = trail.mean();
    if (cursor == null) return;
    final d = delta(box.localToGlobal(box.size.center(Offset.zero)), cursor, box.size,
        View.of(context).devicePixelRatio);
    if (d.dx == null && d.dy == null) return;
    final age = Stopwatch()..start();
    late final Timer t;
    t = Timer(delay, () {
      _pending.remove(t);
      send(d.dx, d.dy, age.elapsedMilliseconds);
    });
    _pending.add(t);
  }

  /// Backspace / Delete: the selections just before were mistakes, do not learn from them.
  void cancelPending() {
    for (final t in _pending) {
      t.cancel();
    }
    _pending.clear();
  }
}
