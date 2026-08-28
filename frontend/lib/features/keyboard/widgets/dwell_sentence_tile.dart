import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/widgets/dwell_wrapper.dart';

/// A sentence tile with dwell auto-select.
class DwellSentenceTile extends StatelessWidget {
  final String text;
  final VoidCallback onTap;
  final bool isAiPrediction;

  const DwellSentenceTile({
    super.key,
    required this.text,
    required this.onTap,
    this.isAiPrediction = false,
  });

  @override
  Widget build(BuildContext context) {
    return DwellWrapper(
      onTap: onTap,
      dwellColor: isAiPrediction ? AppColors.accentCyan : AppColors.dwellRing,
      borderRadius: 4.0,
      builder: (hovering, dwellCtrl) {
        return AnimatedContainer(
          duration: const Duration(milliseconds: 120),
          padding: const EdgeInsets.fromLTRB(14, 14, 14, 20),
          constraints: const BoxConstraints(minHeight: 56),
          decoration: BoxDecoration(
            color: hovering
                ? (isAiPrediction
                    ? AppColors.accentCyan.withOpacity(0.10)
                    : AppColors.sentenceHover)
                : (isAiPrediction
                    ? AppColors.accentCyan.withOpacity(0.04)
                    : AppColors.sentenceBg),
            border: hovering
                ? Border(
                    left: BorderSide(
                      color: isAiPrediction
                          ? AppColors.accentCyan
                          : AppColors.primaryBlue,
                      width: 3,
                    ),
                  )
                : null,
          ),
          child: Row(
            children: [
              if (isAiPrediction) ...[
                Icon(
                  Icons.auto_awesome,
                  size: 12,
                  color: AppColors.accentCyan.withOpacity(0.7),
                ),
                const SizedBox(width: 6),
              ],
              Expanded(
                child: Text(
                  text,
                  style: GoogleFonts.inter(
                    fontSize: 14.5,
                    color: hovering
                        ? (isAiPrediction
                            ? AppColors.accentCyan
                            : AppColors.primaryBlue)
                        : AppColors.sentenceText,
                    fontWeight: hovering || isAiPrediction
                        ? FontWeight.w600
                        : FontWeight.w500,
                  ),
                ),
              ),
              if (hovering)
                Icon(
                  Icons.arrow_forward_ios_rounded,
                  size: 12,
                  color: isAiPrediction
                      ? AppColors.accentCyan.withOpacity(0.6)
                      : AppColors.primaryBlue.withOpacity(0.6),
                ),
            ],
          ),
        );
      },
    );
  }
}
