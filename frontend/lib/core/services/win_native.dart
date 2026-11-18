import 'dart:ffi';
import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';
import 'package:ffi/ffi.dart';

/// Small Windows helpers used to run the eye tracker and the AI backend as background processes.
/// Every function is a harmless no-op on other platforms or if Windows refuses.
class WinNative {
  static final DynamicLibrary? _k32 = Platform.isWindows ? DynamicLibrary.open('kernel32.dll') : null;
  static final DynamicLibrary? _u32 = Platform.isWindows ? DynamicLibrary.open('user32.dll') : null;
  static Pointer<Void>? _job;
  static Pointer<Uint8>? _click;
  static int Function(Pointer<Uint8>, Pointer<Void>, int)? _playSound;

  /// Short soft click (30 ms, 1.5 kHz, fast decay) played from memory with winmm PlaySound, asynchronously, so a
  /// selection is never delayed. Selection feedback for gaze typing (sound + visual, Majaranta et al.).
  static bool playClick() {
    if (!Platform.isWindows) return false;
    try {
      if (_click == null) {
        const rate = 22050, n = 662; // 30 ms
        final wav = calloc<Uint8>(44 + n * 2);
        final b = wav.asTypedList(44 + n * 2).buffer.asByteData();
        void str(int o, String s) => s.codeUnits.asMap().forEach((i, c) => b.setUint8(o + i, c));
        str(0, 'RIFF'); b.setUint32(4, 36 + n * 2, Endian.little); str(8, 'WAVEfmt ');
        b.setUint32(16, 16, Endian.little); b.setUint16(20, 1, Endian.little); b.setUint16(22, 1, Endian.little);
        b.setUint32(24, rate, Endian.little); b.setUint32(28, rate * 2, Endian.little);
        b.setUint16(32, 2, Endian.little); b.setUint16(34, 16, Endian.little);
        str(36, 'data'); b.setUint32(40, n * 2, Endian.little);
        for (var i = 0; i < n; i++) {
          final t = i / rate;
          b.setInt16(44 + i * 2, (0.35 * 32767 * math.sin(2 * math.pi * 1500 * t) * math.exp(-t / 0.006)).round(),
              Endian.little);
        }
        _click = wav; // kept for the life of the app (PlaySound reads it while playing)
        _playSound = DynamicLibrary.open('winmm.dll').lookupFunction<Int32 Function(Pointer<Uint8>, Pointer<Void>, Uint32),
            int Function(Pointer<Uint8>, Pointer<Void>, int)>('PlaySoundW');
      }
      return _playSound!(_click!, nullptr, 0x0001 | 0x0004 | 0x0002) != 0; // SND_ASYNC | SND_MEMORY | SND_NODEFAULT
    } catch (_) {
      return false;
    }
  }

  /// Windows only lets the app in front hand the foreground to another program. Called right before asking the
  /// tracker to open a calibration / Eye View window, so that window gets the keyboard (ESC, Enter, Space).
  static void allowOtherAppToFront() {
    final u = _u32;
    if (u == null) return;
    try {
      final allow = u.lookupFunction<Int32 Function(Uint32), int Function(int)>('AllowSetForegroundWindow');
      allow(0xFFFFFFFF); // ASFW_ANY
    } catch (_) {}
  }

  /// Puts a child process into a Windows job object with KILL_ON_JOB_CLOSE: when this app ends for any reason
  /// (also a crash), Windows ends the child too, so no backend keeps memory or cameras busy. Returns success.
  static bool endWithThisApp(int pid) {
    final k = _k32;
    if (k == null) return false;
    try {
      final closeHandle = k.lookupFunction<Int32 Function(Pointer<Void>), int Function(Pointer<Void>)>('CloseHandle');
      if (_job == null) {
        final createJob = k.lookupFunction<Pointer<Void> Function(Pointer<Void>, Pointer<Utf16>),
            Pointer<Void> Function(Pointer<Void>, Pointer<Utf16>)>('CreateJobObjectW');
        final setInfo = k.lookupFunction<Int32 Function(Pointer<Void>, Int32, Pointer<Void>, Uint32),
            int Function(Pointer<Void>, int, Pointer<Void>, int)>('SetInformationJobObject');
        final job = createJob(nullptr, nullptr);
        if (job == nullptr) return false;
        // JOBOBJECT_EXTENDED_LIMIT_INFORMATION (144 bytes on 64-bit), BasicLimitInformation.LimitFlags at offset 16
        final info = calloc<Uint8>(144);
        try {
          Pointer<Uint32>.fromAddress(info.address + 16).value = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
          if (setInfo(job, 9 /* JobObjectExtendedLimitInformation */, info.cast(), 144) == 0) {
            closeHandle(job);
            return false;
          }
        } finally {
          calloc.free(info);
        }
        _job = job; // kept open for the life of the app: closing it (at exit) ends the children
      }
      final openProcess = k.lookupFunction<Pointer<Void> Function(Uint32, Int32, Uint32),
          Pointer<Void> Function(int, int, int)>('OpenProcess');
      final assign = k.lookupFunction<Int32 Function(Pointer<Void>, Pointer<Void>),
          int Function(Pointer<Void>, Pointer<Void>)>('AssignProcessToJobObject');
      final h = openProcess(0x0101 /* PROCESS_SET_QUOTA | PROCESS_TERMINATE */, 0, pid);
      if (h == nullptr) return false;
      final ok = assign(_job!, h);
      closeHandle(h);
      return ok != 0;
    } catch (_) {
      return false;
    }
  }
}
