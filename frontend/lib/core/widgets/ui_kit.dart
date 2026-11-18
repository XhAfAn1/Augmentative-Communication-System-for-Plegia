import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import '../theme/app_colors.dart';
import 'dwell_wrapper.dart';

/// Shared building blocks so every screen (keyboard, settings, eye tracking, dashboard) looks the same.

// Menu look (Settings / Eye Tracking / Dashboard). Visual only: colours, spacing, typography.
const Color _menuText = Color(0xFF1A2E52); // titles
const Color _menuSubtext = Color(0xFF5F6B7A); // subtitles / hints
const Color _menuHeader = Color(0xFF5B6B82); // section headers
const Color _menuBorder = Color(0xFFE1E8F2); // card borders
const Color _menuDivider = Color(0xFFEEF2F7); // lines between rows
const double menuMaxWidth = 960; // settings content does not stretch over a wide screen

/// Frame for the menu screens: content centred with a fixed maximum width, plus one consistent style for the
/// standard controls inside (switches, sliders, dividers, list rows). Does not change any behaviour.
class MenuPage extends StatelessWidget {
  final Widget child;
  final double maxWidth; // double.infinity = full width (dashboard grid)
  const MenuPage({super.key, required this.child, this.maxWidth = menuMaxWidth});

  @override
  Widget build(BuildContext context) {
    final base = Theme.of(context);
    final theme = base.copyWith(
      dividerTheme: const DividerThemeData(color: _menuDivider, thickness: 1, space: 1),
      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith((s) => s.contains(WidgetState.selected) ? Colors.white : null),
        trackColor: WidgetStateProperty.resolveWith(
            (s) => s.contains(WidgetState.selected) && !s.contains(WidgetState.disabled) ? AppColors.primaryBlue : null),
        trackOutlineColor: WidgetStateProperty.resolveWith(
            (s) => s.contains(WidgetState.selected) ? AppColors.primaryBlue : null),
      ),
      sliderTheme: base.sliderTheme.copyWith(
        trackHeight: 4,
        activeTrackColor: AppColors.primaryBlue,
        thumbColor: AppColors.primaryBlue,
        inactiveTrackColor: const Color(0xFFDCE5F1),
        overlayColor: AppColors.primaryBlue.withValues(alpha: 0.10),
      ),
      listTileTheme: ListTileThemeData(
        contentPadding: const EdgeInsets.symmetric(horizontal: 18, vertical: 4),
        iconColor: AppColors.primaryBlue,
        titleTextStyle: GoogleFonts.inter(fontSize: 14.5, fontWeight: FontWeight.w600, color: _menuText),
        subtitleTextStyle: GoogleFonts.inter(fontSize: 12.5, color: _menuSubtext, height: 1.35),
      ),
    );
    return Theme(
      data: theme,
      child: LayoutBuilder(builder: (context, c) {
        final w = c.maxWidth < maxWidth ? c.maxWidth : maxWidth;
        return Align(alignment: Alignment.topCenter, child: SizedBox(width: w, height: c.maxHeight, child: child));
      }),
    );
  }
}

/// Icon in a small tinted rounded square (row leading icon in the menus).
class MenuIcon extends StatelessWidget {
  final IconData icon;
  final Color color;
  const MenuIcon(this.icon, this.color, {super.key});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 36,
      height: 36,
      decoration: BoxDecoration(color: color.withValues(alpha: 0.10), borderRadius: BorderRadius.circular(9)),
      child: Icon(icon, color: color, size: 20),
    );
  }
}

class SectionHeader extends StatelessWidget {
  final String title;
  const SectionHeader(this.title, {super.key});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 26, bottom: 10, left: 6),
      child: Text(title.toUpperCase(),
          style: GoogleFonts.inter(fontWeight: FontWeight.w700, fontSize: 12, color: _menuHeader, letterSpacing: 1.1)),
    );
  }
}

class SettingCard extends StatelessWidget {
  final List<Widget> children;
  const SettingCard({super.key, required this.children});

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.symmetric(vertical: 2),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: _menuBorder),
        boxShadow: const [BoxShadow(color: Color(0x0A1A2E52), blurRadius: 10, offset: Offset(0, 2))],
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(14),
        child: Material(color: Colors.white, child: Column(children: children)),
      ),
    );
  }
}

class SwitchTile extends StatelessWidget {
  final String title, subtitle;
  final bool value;
  final void Function(bool)? onChanged;
  final IconData icon;
  final Color iconColor;
  const SwitchTile({
    super.key,
    required this.title,
    required this.subtitle,
    required this.value,
    required this.onChanged,
    required this.icon,
    required this.iconColor,
  });

  @override
  Widget build(BuildContext context) {
    return SwitchListTile(
      secondary: MenuIcon(icon, iconColor),
      title: Text(title, style: GoogleFonts.inter(fontSize: 14.5, fontWeight: FontWeight.w600, color: _menuText)),
      subtitle: Padding(
        padding: const EdgeInsets.only(top: 2),
        child: Text(subtitle, style: GoogleFonts.inter(fontSize: 12.5, color: _menuSubtext, height: 1.35)),
      ),
      contentPadding: const EdgeInsets.symmetric(horizontal: 18, vertical: 6),
      value: value,
      onChanged: onChanged, // one switch colour for all rows (MenuPage theme / app primary)
    );
  }
}

/// Slider row in the Settings style: "Title: value" over the slider, optional hint underneath.
class SliderTile extends StatelessWidget {
  final IconData icon;
  final String title;
  final String? hint;
  final double value, min, max;
  final int? divisions;
  final ValueChanged<double>? onChanged;
  final ValueChanged<double>? onChangeEnd;
  const SliderTile({
    super.key,
    required this.icon,
    required this.title,
    required this.value,
    required this.min,
    required this.max,
    this.divisions,
    this.hint,
    this.onChanged,
    this.onChangeEnd,
  });

  @override
  Widget build(BuildContext context) {
    return ListTile(
      leading: MenuIcon(icon, AppColors.primaryBlue),
      contentPadding: const EdgeInsets.symmetric(horizontal: 18, vertical: 6),
      title: Text(title, style: GoogleFonts.inter(fontSize: 14.5, fontWeight: FontWeight.w600, color: _menuText)),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Slider(
            value: value.clamp(min, max).toDouble(),
            min: min,
            max: max,
            divisions: divisions,
            activeColor: AppColors.primaryBlue,
            onChanged: onChanged,
            onChangeEnd: onChangeEnd,
          ),
          if (hint != null) Text(hint!, style: GoogleFonts.inter(fontSize: 12, color: _menuSubtext)),
        ],
      ),
    );
  }
}

/// Small coloured status badge ("● Running").
class StatusPill extends StatelessWidget {
  final String text;
  final Color color;
  const StatusPill(this.text, this.color, {super.key});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(color: color.withValues(alpha: 0.12), borderRadius: BorderRadius.circular(20)),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(Icons.circle, color: color, size: 8),
        const SizedBox(width: 4),
        Text(text, style: GoogleFonts.inter(fontSize: 11, color: color, fontWeight: FontWeight.w700)),
      ]),
    );
  }
}

/// Large gaze-selectable button (dwell or click), same look as the keyboard's Speak / Clear buttons.
class DwellActionButton extends StatelessWidget {
  final String label;
  final String? sublabel;
  final IconData icon;
  final Color color;
  final VoidCallback? onTap; // null = disabled
  final double height;
  const DwellActionButton({
    super.key,
    required this.label,
    required this.icon,
    required this.color,
    required this.onTap,
    this.sublabel,
    this.height = 84,
  });

  @override
  Widget build(BuildContext context) {
    final enabled = onTap != null;
    final c = enabled ? color : Colors.grey;
    Widget body(bool hovering) => AnimatedContainer(
          duration: const Duration(milliseconds: 120),
          height: height,
          padding: const EdgeInsets.symmetric(horizontal: 12),
          decoration: BoxDecoration(
            color: hovering && enabled ? c.withValues(alpha: 0.12) : Colors.white,
            border: Border.all(color: c.withValues(alpha: hovering && enabled ? 0.8 : 0.4), width: hovering ? 2.0 : 1.5),
            borderRadius: BorderRadius.circular(10),
          ),
          child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
            Icon(icon, color: c, size: 26),
            const SizedBox(width: 10),
            Flexible(
              child: Column(mainAxisAlignment: MainAxisAlignment.center, crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(label, style: GoogleFonts.inter(fontSize: 15, fontWeight: FontWeight.w700, color: c)),
                if (sublabel != null)
                  Text(sublabel!,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: GoogleFonts.inter(fontSize: 11, color: Colors.black54)),
              ]),
            ),
          ]),
        );
    if (!enabled) return body(false);
    return DwellWrapper(onTap: onTap!, dwellColor: color, borderRadius: 10, builder: (hovering, _) => body(hovering));
  }
}
