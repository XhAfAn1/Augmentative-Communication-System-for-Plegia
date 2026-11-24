import 'dart:async';
import 'package:flutter/material.dart';
import '../../core/constants/app_constants.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/dwell_wrapper.dart';
import '../../core/services/win_native.dart';
import '../../core/services/gaze_offset_learner.dart';
import '../../core/services/gaze_targets.dart';

/// Increment this to instantly fire whatever key the cursor is currently over.
/// Driven by the Spacebar listener in keyboard_screen.dart.
final ValueNotifier<int> spacebarTrigger = ValueNotifier<int>(0);
// Global toggle for Flutter spacebar handling
final ValueNotifier<bool> flutterSpacebarEnabled = ValueNotifier<bool>(true);
// Global toggle for Dwell clicking
final ValueNotifier<bool> flutterDwellEnabled = ValueNotifier<bool>(true);
// Settings > Selection Sound: a short click on every selection (dwell, Spacebar or mouse)
final ValueNotifier<bool> selectionSoundEnabled = ValueNotifier<bool>(true);
void selectionFeedback() {
  if (selectionSoundEnabled.value) WinNative.playClick();
}


class GazeKeyboard extends StatelessWidget {
  final bool isUpperCase;
  final bool isNumbers;
  final void Function(String) addChar;
  final VoidCallback toggleShift;
  final VoidCallback toggleNumbers;
  final VoidCallback backspace;
  final VoidCallback deleteLastWord;
  final VoidCallback addSpace;
  final VoidCallback newLine;
  final ValueNotifier<int>? spacebarNotifier;

  const GazeKeyboard({
    super.key,
    required this.isUpperCase,
    required this.isNumbers,
    required this.addChar,
    required this.toggleShift,
    required this.toggleNumbers,
    required this.backspace,
    required this.deleteLastWord,
    required this.addSpace,
    required this.newLine,
    this.spacebarNotifier,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(AppConstants.keyboardPadding),
      decoration: BoxDecoration(
        color: AppColors.keyboardDark,
        borderRadius: BorderRadius.circular(16),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.28),
            blurRadius: 12,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: Column(
        children: [
          Expanded(child: _row1()),
          const SizedBox(height: AppConstants.keySpacing),
          Expanded(child: _row2()),
          const SizedBox(height: AppConstants.keySpacing),
          Expanded(child: _row3()),
          const SizedBox(height: AppConstants.keySpacing),
          Expanded(child: _row4()),
        ],
      ),
    );
  }

  // ── Row helpers ─────────────────────────────────────────────
  Widget _gap() => const SizedBox(width: AppConstants.keySpacing);

  Widget _letterKey(String l) {
    final display = isUpperCase ? l.toUpperCase() : l.toLowerCase();
    final key = KeyboardKey(
      label: l,
      displayLabel: display,
      onPressed: () => addChar(display),
      spacebarNotifier: spacebarNotifier ?? spacebarTrigger,
    );
    // Level 2 points (GazeTargets): Q, P, Z, ".", G
    final name = l.trim();
    return Expanded(
      child: const {'Q', 'P', 'Z', '.', 'G'}.contains(name)
          ? KeyedSubtree(key: GazeTargets.key(name), child: key)
          : key,
    );
  }

  // Q W E R T Y U I O P
  Widget _row1() {
    const letters = ['Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P'];
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (int i = 0; i < letters.length; i++) ...[
          if (i > 0) _gap(),
          _letterKey(letters[i]),
        ],
      ],
    );
  }

  // A S D F G H J K L
  Widget _row2() {
    const letters = ['A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L'];
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (int i = 0; i < letters.length; i++) ...[
          if (i > 0) _gap(),
          _letterKey(letters[i]),
        ],
      ],
    );
  }

  // Z X C V B N M , .
  Widget _row3() {
    const letters = ['Z', 'X', 'C', 'V', 'B', 'N', 'M', ', ', '. '];
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (int i = 0; i < letters.length; i++) ...[
          if (i > 0) _gap(),
          _letterKey(letters[i]),
        ],
      ],
    );
  }

  //  ⌫   [   Space   ]  Delete
  Widget _row4() {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        KeyboardKey(
          label: 'bs',
          keyType: KeyType.backspace,
          fixedWidth: 200,
          bgColor: AppColors.keyBgSpecial,
          onPressed: backspace,
          spacebarNotifier: spacebarNotifier ?? spacebarTrigger,
        ),
        _gap(),
        Expanded(
          child: KeyedSubtree(
            key: GazeTargets.key('Space'), // Level 2 point
            child: KeyboardKey(
              label: 'space',
              displayLabel: 'Space',
              keyType: KeyType.space,
              onPressed: addSpace,
              spacebarNotifier: spacebarNotifier ?? spacebarTrigger,
            ),
          ),
        ),
        _gap(),
        KeyboardKey(
          label: 'Delete',
          displayLabel: 'Delete',
          keyType: KeyType.delete,
          fixedWidth: 200,
          bgColor: AppColors.keyBgSpecial,
          fgColor: AppColors.keyTextSpecial,
          onPressed: deleteLastWord,
          spacebarNotifier: spacebarNotifier ?? spacebarTrigger,
        ),
      ],
    );
  }
}

// --- End of original file ---


enum KeyType { letter, special, shift, space, backspace, enter, delete, globe }

class KeyboardKey extends StatefulWidget {
  final String label;
  final String? displayLabel;
  final KeyType keyType;
  final VoidCallback? onPressed;
  final double? fixedWidth;
  final Color? bgColor;
  final Color? fgColor;
  final bool dwellEnabled;
  final ValueNotifier<int>? spacebarNotifier;

  const KeyboardKey({
    super.key,
    required this.label,
    this.displayLabel,
    this.keyType = KeyType.letter,
    this.onPressed,
    this.fixedWidth,
    this.bgColor,
    this.fgColor,
    this.dwellEnabled = true,
    this.spacebarNotifier,
  });

  @override
  State<KeyboardKey> createState() => _KeyboardKeyState();
}

class _KeyboardKeyState extends State<KeyboardKey>
    with SingleTickerProviderStateMixin {
  late AnimationController _dwellCtrl;
  bool _hovering = false;
  final HoverTrail _trail = HoverTrail(); // where the cursor rested (Auto re-centre)
  bool _pressed = false;

  @override
  void initState() {
    super.initState();
    _dwellCtrl = AnimationController(
      vsync: this,
      duration: Duration(milliseconds: dwellDurationMs.value), // the Settings Dwell Time slider
    )..addStatusListener((s) {
        if (s == AnimationStatus.completed) _onDwellFired();
      });
    widget.spacebarNotifier?.addListener(_onSpacebarTrigger);
    dwellDurationMs.addListener(_onDwellTimeChanged);
  }

  void _onDwellTimeChanged() => _dwellCtrl.duration = Duration(milliseconds: dwellDurationMs.value);

  void _onSpacebarTrigger() {
    // Fire instantly only if the cursor is currently hovering over this key
    if (_hovering) {
      _dwellCtrl.reset();
      _learn();
      _triggerPress();
    }
  }

  /// Auto re-centre: a dwell / Spacebar selection teaches the headset slip (not a mouse click).
  /// Backspace / Delete mean the selections just before were mistakes: those are not sent.
  void _learn() {
    if (widget.keyType == KeyType.backspace || widget.keyType == KeyType.delete) {
      GazeOffsetLearner.instance.cancelPending();
    }
    GazeOffsetLearner.instance.selected(context, _trail);
  }

  @override
  void dispose() {
    widget.spacebarNotifier?.removeListener(_onSpacebarTrigger);
    dwellDurationMs.removeListener(_onDwellTimeChanged);
    _dwellCtrl.dispose();
    super.dispose();
  }

  void _onDwellFired() {
    _learn();
    _triggerPress();
    _dwellCtrl.reset();
  }

  void _triggerPress() {
    selectionFeedback();
    widget.onPressed?.call();
    setState(() => _pressed = true);
    Future.delayed(const Duration(milliseconds: 120), () {
      if (mounted) setState(() => _pressed = false);
    });
  }

  Color get _bg {
    if (widget.bgColor != null) return widget.bgColor!;
    return switch (widget.keyType) {
      KeyType.letter || KeyType.space => AppColors.keyBg,
      KeyType.enter => AppColors.primaryBlue,
      _ => AppColors.keyBgSpecial,
    };
  }

  Color get _fg {
    if (widget.fgColor != null) return widget.fgColor!;
    return switch (widget.keyType) {
      KeyType.enter => Colors.white,
      _ => AppColors.keyText,
    };
  }

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (e) {
        if (!widget.dwellEnabled) return;
        _trail
          ..clear()
          ..add(e.position);
        setState(() => _hovering = true);
        if (flutterDwellEnabled.value) {
          _dwellCtrl.forward();
        }
      },
      onHover: (e) => _trail.add(e.position),
      onExit: (_) {
        setState(() => _hovering = false);
        _dwellCtrl.reset();
      },
      child: GestureDetector(
        onTap: () {
          _dwellCtrl.reset();
          _triggerPress();
        },
        child: AnimatedBuilder(
          animation: _dwellCtrl,
          builder: (context, child) {
            return Stack(
              children: [
                child!,
                if (_hovering && _dwellCtrl.value > 0)
                  Positioned.fill(
                    child: CustomPaint(
                      painter: DwellPainter(
                        progress: _dwellCtrl.value,
                        color: AppColors.dwellRing,
                        radius: AppConstants.keyRadius,
                      ),
                    ),
                  ),
              ],
            );
          },
          // hover look as on the other buttons (word chips): blue border + tint + small lift, so the user
          // always sees which key the gaze cursor is on, with dwell on or off
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 80),
            width: widget.fixedWidth,
            transform: _hovering && !_pressed ? (Matrix4.identity()..translate(0.0, -1.5)) : Matrix4.identity(),
            transformAlignment: Alignment.center,
            decoration: BoxDecoration(
              color: _pressed
                  ? (_bg == AppColors.keyBg
                      ? AppColors.primaryBlueLight
                      : _bg.withOpacity(0.75))
                  : _hovering
                      ? Color.alphaBlend(AppColors.primaryBlue.withOpacity(0.10), _bg)
                      : _bg,
              border: Border.all(
                color: !_hovering
                    ? Colors.transparent
                    : (_bg == AppColors.primaryBlue ? AppColors.dwellRing : AppColors.primaryBlue), // Enter is blue

                width: 1.8,
              ),
              borderRadius: BorderRadius.circular(AppConstants.keyRadius),
              boxShadow: [
                BoxShadow(
                  color: _hovering && !_pressed
                      ? AppColors.primaryBlue.withOpacity(0.25)
                      : Colors.black.withOpacity(_pressed ? 0.05 : 0.18),
                  blurRadius: _pressed ? 1 : (_hovering ? 8 : 3),
                  offset: Offset(0, _pressed ? 0 : (_hovering ? 3 : 2)),
                ),
              ],
            ),
            child: Center(child: _label()),
          ),
        ),
      ),
    );
  }

  Widget _label() {
    final display = widget.displayLabel ?? widget.label;
    return switch (widget.keyType) {
      KeyType.backspace =>
        Icon(Icons.backspace_outlined, color: _fg, size: 22),
      KeyType.shift => Icon(
          _pressed ? Icons.keyboard_capslock : Icons.keyboard_arrow_up_rounded,
          color: _fg,
          size: 26,
        ),
      KeyType.globe => Icon(Icons.language, color: _fg, size: 22),
      _ => Text(
          display,
          style: TextStyle(
            color: _pressed && widget.keyType == KeyType.letter
                ? Colors.white
                : _fg,
            fontSize: widget.keyType == KeyType.letter ? 22 : 15,
            fontWeight: FontWeight.w700,
            height: 1,
          ),
        ),
    };
  }
}



// --- End of original file ---




// --- End of original file ---

