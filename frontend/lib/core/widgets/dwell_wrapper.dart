import 'package:flutter/material.dart';
import '../constants/app_constants.dart';
import '../theme/app_colors.dart';

/// Draws a rounded-rect progress ring for dwell selection.
/// Shared by all interactive widgets (words, sentences, buttons).
class DwellPainter extends CustomPainter {
  final double progress;
  final Color color;
  final double radius;

  const DwellPainter({
    required this.progress,
    required this.color,
    required this.radius,
  });

  @override
  void paint(Canvas canvas, Size size) {
    if (progress <= 0) return;
    final paint = Paint()
      ..color = color
      ..strokeWidth = 3.0
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;

    final path = Path()
      ..addRRect(RRect.fromRectAndRadius(
        Rect.fromLTWH(1.5, 1.5, size.width - 3, size.height - 3),
        Radius.circular(radius),
      ));

    for (final metric in path.computeMetrics()) {
      canvas.drawPath(metric.extractPath(0, metric.length * progress), paint);
    }
  }

  @override
  bool shouldRepaint(DwellPainter old) => old.progress != progress;
}

/// A wrapper that adds dwell auto-select behavior to any child widget.
/// When a user hovers over the widget for [dwellDuration], it auto-fires [onTap].
class DwellWrapper extends StatefulWidget {
  final Widget Function(bool hovering, AnimationController dwellCtrl) builder;
  final VoidCallback onTap;
  final Duration dwellDuration;
  final Color dwellColor;
  final double borderRadius;

  const DwellWrapper({
    super.key,
    required this.builder,
    required this.onTap,
    this.dwellDuration = const Duration(milliseconds: AppConstants.dwellDurationMs),
    this.dwellColor = AppColors.dwellRing,
    this.borderRadius = 8.0,
  });

  @override
  State<DwellWrapper> createState() => _DwellWrapperState();
}

class _DwellWrapperState extends State<DwellWrapper>
    with SingleTickerProviderStateMixin {
  late AnimationController _dwellCtrl;
  bool _hovering = false;
  bool _pressed = false;

  @override
  void initState() {
    super.initState();
    _dwellCtrl = AnimationController(
      vsync: this,
      duration: widget.dwellDuration,
    )..addStatusListener((s) {
        if (s == AnimationStatus.completed) {
          _onDwellFired();
        }
      });
  }

  @override
  void dispose() {
    _dwellCtrl.dispose();
    super.dispose();
  }

  void _onDwellFired() {
    widget.onTap();
    _dwellCtrl.reset();
    setState(() => _pressed = true);
    Future.delayed(const Duration(milliseconds: 120), () {
      if (mounted) setState(() => _pressed = false);
    });
  }

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) {
        setState(() => _hovering = true);
        _dwellCtrl.forward();
      },
      onExit: (_) {
        setState(() {
          _hovering = false;
          _pressed = false;
        });
        _dwellCtrl.reset();
      },
      child: GestureDetector(
        onTap: () {
          _dwellCtrl.reset();
          widget.onTap();
          setState(() => _pressed = true);
          Future.delayed(const Duration(milliseconds: 120), () {
            if (mounted) setState(() => _pressed = false);
          });
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
                        color: widget.dwellColor,
                        radius: widget.borderRadius,
                      ),
                    ),
                  ),
              ],
            );
          },
          child: widget.builder(_hovering, _dwellCtrl),
        ),
      ),
    );
  }
}
