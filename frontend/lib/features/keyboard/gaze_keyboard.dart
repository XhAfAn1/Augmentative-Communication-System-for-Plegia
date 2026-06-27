import 'dart:async';
import 'package:flutter/material.dart';
import '../../core/constants/app_constants.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/dwell_wrapper.dart';


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
    return Expanded(
      child: KeyboardKey(
        label: l,
        displayLabel: display,
        onPressed: () => addChar(display),
      ),
    );
  }

  // Q W E R T Y U I O P ⌫
  Widget _row1() {
    const letters = ['Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P'];
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (int i = 0; i < letters.length; i++) ...[
          if (i > 0) _gap(),
          _letterKey(letters[i]),
        ],
        _gap(),
        KeyboardKey(
          label: 'bs',
          keyType: KeyType.backspace,
          fixedWidth: 70,
          bgColor: AppColors.keyBgSpecial,
          onPressed: backspace,
        ),
      ],
    );
  }

  // A S D F G H J K L Enter
  Widget _row2() {
    const letters = ['A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L'];
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (int i = 0; i < letters.length; i++) ...[
          if (i > 0) _gap(),
          _letterKey(letters[i]),
        ],
        _gap(),
        KeyboardKey(
          label: 'Enter',
          keyType: KeyType.enter,
          fixedWidth: 92,
          onPressed: newLine,
        ),
      ],
    );
  }

  // ⇧  Z X C V B N M , .
  Widget _row3() {
    const letters = ['Z', 'X', 'C', 'V', 'B', 'N', 'M', ', ', '. '];
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        KeyboardKey(
          label: 'shift',
          keyType: KeyType.shift,
          fixedWidth: 70,
          bgColor: isUpperCase ? AppColors.primaryBlue : AppColors.keyBgSpecial,
          fgColor: isUpperCase ? Colors.white : AppColors.keyTextSpecial,
          onPressed: toggleShift,
        ),
        _gap(),
        for (int i = 0; i < letters.length; i++) ...[
          if (i > 0) _gap(),
          _letterKey(letters[i]),
        ],
      ],
    );
  }

  // 123 🌐  [   Space   ]  Delete
  Widget _row4() {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        KeyboardKey(
          label: isNumbers ? 'ABC' : '123',
          keyType: KeyType.special,
          fixedWidth: 62,
          bgColor: isNumbers ? AppColors.primaryBlue : AppColors.keyBgSpecial,
          fgColor: isNumbers ? Colors.white : AppColors.keyTextSpecial,
          onPressed: toggleNumbers,
        ),
        _gap(),
        KeyboardKey(
          label: 'globe',
          keyType: KeyType.globe,
          fixedWidth: 62,
          bgColor: AppColors.keyBgSpecial,
          fgColor: AppColors.keyTextSpecial,
          onPressed: () {},
        ),
        _gap(),
        Expanded(
          child: KeyboardKey(
            label: 'space',
            displayLabel: 'Space',
            keyType: KeyType.space,
            onPressed: addSpace,
          ),
        ),
        _gap(),
        KeyboardKey(
          label: 'Delete',
          displayLabel: 'Delete',
          keyType: KeyType.delete,
          fixedWidth: 92,
          bgColor: AppColors.keyBgSpecial,
          fgColor: AppColors.keyTextSpecial,
          onPressed: deleteLastWord,
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
  });

  @override
  State<KeyboardKey> createState() => _KeyboardKeyState();
}

class _KeyboardKeyState extends State<KeyboardKey>
    with SingleTickerProviderStateMixin {
  late AnimationController _dwellCtrl;
  bool _hovering = false;
  bool _pressed = false;

  @override
  void initState() {
    super.initState();
    _dwellCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: AppConstants.dwellDurationMs),
    )..addStatusListener((s) {
        if (s == AnimationStatus.completed) _onDwellFired();
      });
  }

  @override
  void dispose() {
    _dwellCtrl.dispose();
    super.dispose();
  }

  void _onDwellFired() {
    _triggerPress();
    _dwellCtrl.reset();
  }

  void _triggerPress() {
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
      onEnter: (_) {
        if (!widget.dwellEnabled) return;
        setState(() => _hovering = true);
        _dwellCtrl.forward();
      },
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
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 80),
            width: widget.fixedWidth,
            decoration: BoxDecoration(
              color: _pressed
                  ? (_bg == AppColors.keyBg
                      ? AppColors.primaryBlueLight
                      : _bg.withOpacity(0.75))
                  : _bg,
              borderRadius: BorderRadius.circular(AppConstants.keyRadius),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withOpacity(_pressed ? 0.05 : 0.18),
                  blurRadius: _pressed ? 1 : 3,
                  offset: Offset(0, _pressed ? 0 : 2),
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

