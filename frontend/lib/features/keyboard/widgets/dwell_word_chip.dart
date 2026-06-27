import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/widgets/dwell_wrapper.dart';

/// A word chip with dwell auto-select (progress ring fires onTap after hover duration).
class DwellWordChip extends StatelessWidget {
  final String label;
  final VoidCallback onTap;
  final bool isPrediction;
  final bool isTopPick;

  const DwellWordChip({
    super.key,
    required this.label,
    required this.onTap,
    this.isPrediction = false,
    this.isTopPick = false,
  });

  @override
  Widget build(BuildContext context) {
    final bool hl = isTopPick;

    return DwellWrapper(
      onTap: onTap,
      dwellColor: hl ? AppColors.accentCyan : AppColors.dwellRing,
      borderRadius: 8.0,
      builder: (hovering, dwellCtrl) {
        return AnimatedContainer(
          duration: const Duration(milliseconds: 120),
          height: 44,
          transform: hovering
              ? (Matrix4.identity()..translate(0.0, -1.5))
              : Matrix4.identity(),
          transformAlignment: Alignment.center,
          decoration: BoxDecoration(
            color: isPrediction
                ? (hl
                    ? (hovering
                        ? AppColors.accentCyan.withOpacity(0.18)
                        : AppColors.accentCyan.withOpacity(0.10))
                    : (hovering
                        ? AppColors.primaryBlue.withOpacity(0.10)
                        : AppColors.quickWordBg))
                : (hovering
                    ? AppColors.primaryBlue.withOpacity(0.10)
                    : AppColors.quickWordBg),
            border: Border.all(
              color: hovering
                  ? (isPrediction && hl
                      ? AppColors.accentCyan
                      : AppColors.primaryBlue)
                  : (isPrediction && hl
                      ? AppColors.accentCyan.withOpacity(0.6)
                      : AppColors.quickWordBorder),
              width: hovering ? 1.8 : 1.5,
            ),
            borderRadius: BorderRadius.circular(8),
            boxShadow: hovering
                ? [
                    BoxShadow(
                      color: isPrediction && hl
                          ? AppColors.accentCyan.withOpacity(0.2)
                          : AppColors.primaryBlue.withOpacity(0.15),
                      blurRadius: 8,
                      offset: const Offset(0, 3),
                    ),
                  ]
                : null,
          ),
          child: Center(
            child: Text(
              label,
              style: GoogleFonts.inter(
                fontSize: 14,
                fontWeight: FontWeight.w700,
                color: hovering
                    ? (isPrediction && hl
                        ? AppColors.accentCyan
                        : AppColors.primaryBlue)
                    : (isPrediction && hl
                        ? AppColors.accentCyan
                        : AppColors.quickWordText),
              ),
            ),
          ),
        );
      },
    );
  }
}
