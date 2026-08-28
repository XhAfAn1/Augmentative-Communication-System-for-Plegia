import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:http/http.dart' as http;
import '../../core/constants/app_constants.dart';
import '../../core/theme/app_colors.dart';
import '../../core/utils/tts_service.dart';
import '../../core/widgets/dwell_wrapper.dart';
import 'widgets/dwell_word_chip.dart';
import 'widgets/dwell_sentence_tile.dart';

import 'gaze_keyboard.dart';

class KeyboardScreen extends StatefulWidget {
  const KeyboardScreen({super.key});

  @override
  State<KeyboardScreen> createState() => _KeyboardScreenState();
}

class _KeyboardScreenState extends State<KeyboardScreen> {
  final TtsService _tts = TtsService();
  bool _isSpeaking = false;

  String _output = '';
  bool _isShift = false;
  bool _isCaps = false;
  bool _isNumbers = false;
  final List<String> _history = [];

  List<Map<String, String>> _predictions = [];
  List<String> _sentencePredictions = [];
  Timer? _debounceTimer;
  Timer? _sentenceDebounceTimer;

  bool get _isUpperCase => _isShift || _isCaps;

  @override
  void initState() {
    super.initState();
    _tts.init();
    HardwareKeyboard.instance.addHandler(_onHardwareKey);
  }

  bool _onHardwareKey(KeyEvent event) {
    if (event is KeyDownEvent &&
        event.logicalKey == LogicalKeyboardKey.space &&
        flutterSpacebarEnabled.value) {
      spacebarTrigger.value++;
      return true; // consume the event
    }
    return false;
  }

  @override
  void dispose() {
    HardwareKeyboard.instance.removeHandler(_onHardwareKey);
    _tts.stop();
    super.dispose();
  }

  Future<void> _speak(String text) async {
    if (text.trim().isEmpty) return;
    
    http.post(
      Uri.parse('http://127.0.0.1:8000/api/submit_sentence'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'text': text.trim()}),
    ).then((_) {}).catchError((e) { debugPrint('Failed to submit: $e'); });

    setState(() => _isSpeaking = true);
    await _tts.speak(text);
    if (mounted) setState(() => _isSpeaking = false);
  }


  void _addChar(String c) {
    setState(() {
      _output += c;
      if (_isShift && !_isCaps) {
        _isShift = false;
      }
    });
    _debouncedFetchPredictions();
  }

  void _toggleShift() {
    setState(() {
      if (_isShift) {
        _isShift = false;
        _isCaps = true;
      } else if (_isCaps) {
        _isCaps = false;
      } else {
        _isShift = true;
      }
    });
  }

  void _toggleNumbers() {
    setState(() {
      _isNumbers = !_isNumbers;
    });
  }

  void _backspace() {
    if (_output.isNotEmpty) {
      setState(() {
        _output = _output.substring(0, _output.length - 1);
      });
      _debouncedFetchPredictions();
    }
  }

  void _deleteLastWord() {
    setState(() {
      if (_output.trim().isEmpty) {
        _output = '';
      } else {
        final lastSpace = _output.trimRight().lastIndexOf(' ');
        if (lastSpace != -1) {
          _output = _output.substring(0, lastSpace + 1);
        } else {
          _output = '';
        }
      }
    });
    _debouncedFetchPredictions();
  }

  void _addSpace() {
    setState(() {
      _output += ' ';
    });
    _fetchNextWordPredictions();
    _debouncedFetchSentencePredictions();
  }

  void _newLine() {
    setState(() {
      _output += '\n';
    });
    _fetchNextWordPredictions();
    _debouncedFetchSentencePredictions();
  }

  void _clear() {
    setState(() {
      _output = '';
      _predictions = [];
      _sentencePredictions = [];
    });
    _debounceTimer?.cancel();
    _sentenceDebounceTimer?.cancel();
    _debouncedFetchSentencePredictions();
  }

  void _saveText() {
    if (_output.trim().isNotEmpty) {
      final textToSubmit = _output.trim();
      http.post(
        Uri.parse('http://127.0.0.1:8000/api/submit_sentence'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'text': textToSubmit}),
      ).then((_) {}).catchError((e) { debugPrint('Failed to submit: $e'); });

      setState(() {
        _history.add(_output.trim());
        _output = '';
        _predictions = [];
        _sentencePredictions = [];
      });
      _debouncedFetchSentencePredictions();
    }
  }

  void _addWord(String word) {
    setState(() {
      if (_output.isNotEmpty && !_output.endsWith(' ') && !_output.endsWith('\n')) _output += ' ';
      _output += word;
    });
    _fetchNextWordPredictions();
    _debouncedFetchSentencePredictions();
  }

  void _acceptPrediction(String word) {
    final lastSpaceIdx = _output.lastIndexOf(' ');
    final lastNewlineIdx = _output.lastIndexOf('\n');
    final splitIdx = lastSpaceIdx > lastNewlineIdx ? lastSpaceIdx : lastNewlineIdx;

    String contextBeforePartial;
    if (splitIdx == -1) {
      contextBeforePartial = '';
    } else {
      contextBeforePartial = _output.substring(0, splitIdx + 1);
    }

    setState(() {
      if (_output.isNotEmpty && !_output.endsWith(' ') && !_output.endsWith('\n')) {
        _output = '$contextBeforePartial$word ';
      } else {
        _output += '$word ';
      }
      _predictions = [];
    });
    _fetchNextWordPredictions();
    _debouncedFetchSentencePredictions();
  }

  void _addSentence(String sentence) {
    setState(() {
      final lastNewlineIdx = _output.lastIndexOf('\n');
      if (lastNewlineIdx == -1) {
        _output = sentence + ' ';
      } else {
        _output = _output.substring(0, lastNewlineIdx + 1) + sentence + ' ';
      }
      _predictions = [];
      _sentencePredictions = [];
    });
    _fetchNextWordPredictions();
    _debouncedFetchSentencePredictions();
  }

  void _debouncedFetchPredictions() {
    _debounceTimer?.cancel();
    if (_output.trim().isEmpty) {
      setState(() {
        _predictions = [];
      });
      return;
    }
    // Reduced from 200ms to 50ms for near-instant typing feedback
    _debounceTimer = Timer(const Duration(milliseconds: 50), () {
      _fetchPrefixPredictions();
    });
  }

  void _debouncedFetchSentencePredictions() {
    _sentenceDebounceTimer?.cancel();
    if (_output.trim().isEmpty) {
      setState(() {
        _sentencePredictions = [];
      });
      return;
    }
    _sentenceDebounceTimer = Timer(const Duration(milliseconds: 800), () {
      _fetchSentencePredictions();
    });
  }

  Future<void> _fetchPrefixPredictions() async {
    final currentText = _output;
    final lastWord = currentText.split(RegExp(r'\s+')).last;
    if (lastWord.isEmpty) return;

    try {
      final response = await http.post(
        Uri.parse('http://127.0.0.1:8000/api/type'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'text': lastWord}),
      );

      // Guard: only update if the user hasn't typed more letters while waiting
      if (response.statusCode == 200 && mounted && _output == currentText) {
        final data = jsonDecode(response.body);
        final suggestions = data['suggestions'] as List;
        setState(() {
          _predictions = suggestions.map((e) => {
            'word': e['word'].toString(),
            'source': e['source'].toString(),
          }).toList();
        });
      }
    } catch (e) {
      debugPrint('Failed to fetch prefix predictions: $e');
    }
  }

  void _fetchNextWordPredictions() {
    final currentText = _output;
    if (currentText.trim().isEmpty) {
      setState(() {
        _predictions = [];
      });
      return;
    }

    // 1. Instant N-gram request
    http.post(
      Uri.parse('http://127.0.0.1:8000/api/spacebar_fast'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'text': currentText}),
    ).then((response) {
      if (response.statusCode == 200 && mounted && _output == currentText) {
        final data = jsonDecode(response.body);
        final suggestions = data['suggestions'] as List;
        setState(() {
          _predictions = suggestions.map((e) => {
            'word': e['word'].toString(),
            'source': e['source'].toString(),
          }).toList();
        });
      }
    }).then((_) {}).catchError((e) { debugPrint('Failed fast: $e'); });

    // 2. Heavy LLM background request
    http.post(
      Uri.parse('http://127.0.0.1:8000/api/spacebar'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'text': currentText}),
    ).then((response) {
      if (response.statusCode == 200 && mounted && _output == currentText) {
        final data = jsonDecode(response.body);
        final suggestions = data['suggestions'] as List;
        setState(() {
          _predictions = suggestions.map((e) => {
            'word': e['word'].toString(),
            'source': e['source'].toString(),
          }).toList();
        });
      }
    }).then((_) {}).catchError((e) { debugPrint('Failed slow: $e'); });
  }

  Future<void> _fetchSentencePredictions() async {
    try {
      final response = await http.post(
        Uri.parse('http://127.0.0.1:8000/api/complete_sentence'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'text': _output.trim()}),
      );

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        final suggestions = data['suggestions'] as List;
        if (mounted) {
          setState(() {
            _sentencePredictions = suggestions.map((e) => e.toString()).toList();
          });
        }
      }
    } catch (e) {
      debugPrint('Failed to fetch sentence predictions: $e');
    }
  }

  @override
  Widget build(BuildContext context) {
    Widget buildDwellFuncBtn({required String label, required IconData icon, required Color color, required VoidCallback onTap, double? height}) {
      return DwellWrapper(
        onTap: onTap,
        dwellColor: color,
        borderRadius: 10.0,
        builder: (hovering, dwellCtrl) {
          return AnimatedContainer(
            duration: const Duration(milliseconds: 120),
            height: height ?? 60,
            transform: hovering
                ? (Matrix4.identity()..translate(0.0, -2.0))
                : Matrix4.identity(),
            transformAlignment: Alignment.center,
            decoration: BoxDecoration(
              color: hovering
                  ? color.withOpacity(0.12)
                  : Colors.white,
              border: Border.all(
                color: hovering
                    ? color.withOpacity(0.8)
                    : color.withOpacity(0.4),
                width: hovering ? 2.0 : 1.5,
              ),
              borderRadius: BorderRadius.circular(10),
              boxShadow: hovering
                  ? [
                      BoxShadow(
                        color: color.withOpacity(0.25),
                        blurRadius: 10,
                        offset: const Offset(0, 4),
                      ),
                    ]
                  : null,
            ),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(icon, color: color, size: 20),
                const SizedBox(width: 6),
                Text(
                  label,
                  style: GoogleFonts.inter(
                      fontSize: 13,
                      fontWeight: FontWeight.w700,
                      color: color),
                ),
              ],
            ),
          );
        },
      );
    }
    return Scaffold(
      backgroundColor: AppColors.appBackground,
      body: Column(
        children: [
          // ── Top bar ────────────────────────────────────────────
          // Container(
          //   height: 50,
          //   padding: const EdgeInsets.symmetric(horizontal: 16),
          //   decoration: BoxDecoration(
          //     color: AppColors.keyboardDark,
          //     boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.25), blurRadius: 6)],
          //   ),
          //   child: Row(
          //     children: [
          //       // Logo
          //       Text(
          //         'AAC ',
          //         style: GoogleFonts.inter(
          //             color: Colors.white, fontSize: 20, fontWeight: FontWeight.w800),
          //       ),
          //       Text(
          //         'System',
          //         style: GoogleFonts.inter(
          //             color: AppColors.accentCyan, fontSize: 20, fontWeight: FontWeight.w800),
          //       ),
          //       const SizedBox(width: 6),
          //       Text(
          //         '— Assistive Communication System',
          //         style: GoogleFonts.inter(
          //             color: Colors.white54, fontSize: 12, fontWeight: FontWeight.w400),
          //       ),
          //       const Spacer(),
          //       // Status badges
          //       Row(
          //         mainAxisSize: MainAxisSize.min,
          //         children: [
          //           Icon(Icons.remove_red_eye_outlined, color: AppColors.statusOffline, size: 15),
          //           const SizedBox(width: 4),
          //           Text('Eye Camera',
          //               style: GoogleFonts.inter(color: AppColors.statusOffline, fontSize: 11, fontWeight: FontWeight.w600)),
          //         ],
          //       ),
          //       const SizedBox(width: 8),
          //       Row(
          //         mainAxisSize: MainAxisSize.min,
          //         children: [
          //           Icon(Icons.psychology_outlined, color: AppColors.statusOffline, size: 15),
          //           const SizedBox(width: 4),
          //           Text('EEG',
          //               style: GoogleFonts.inter(color: AppColors.statusOffline, fontSize: 11, fontWeight: FontWeight.w600)),
          //         ],
          //       ),
          //       const SizedBox(width: 8),
          //       Row(
          //         mainAxisSize: MainAxisSize.min,
          //         children: [
          //           Icon(Icons.surround_sound_outlined, color: _isSpeaking ? AppColors.statusWarning : AppColors.statusOnline, size: 15),
          //           const SizedBox(width: 4),
          //           Text(_isSpeaking ? 'Speaking…' : 'TTS Ready',
          //               style: GoogleFonts.inter(color: _isSpeaking ? AppColors.statusWarning : AppColors.statusOnline, fontSize: 11, fontWeight: FontWeight.w600)),
          //         ],
          //       ),
          //       const SizedBox(width: 12),
          //       // Dashboard button
          //       TextButton.icon(
          //         onPressed: () => context.push('/dashboard'),
          //         icon: const Icon(Icons.dashboard_outlined, color: Colors.white70, size: 18),
          //         label: Text('Dashboard',
          //             style: GoogleFonts.inter(color: Colors.white70, fontSize: 12)),
          //       ),
          //     ],
          //   ),
          // ),
          // ── Main content ───────────────────────────────────────
          Expanded(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(12, 8, 12, 12),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  // Left: output + quick words + keyboard
                  Expanded(
                    flex: 70,
                    child: Column(
                      children: [
                        // ── OutputPanel ───────────────────────────────────────
                        Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            // Header bar
                            Container(
                              padding: const EdgeInsets.symmetric(vertical: 10),
                              decoration: const BoxDecoration(
                                color: AppColors.panelHeader,
                                borderRadius: BorderRadius.vertical(top: Radius.circular(10)),
                              ),
                              child: Text(
                                'Output',
                                textAlign: TextAlign.center,
                                style: GoogleFonts.inter(
                                  color: Colors.white,
                                  fontSize: 17,
                                  fontWeight: FontWeight.w700,
                                  letterSpacing: 0.5,
                                ),
                              ),
                            ),
                            // Text display area
                            Container(
                              height: 120,
                              alignment: Alignment.centerLeft,
                              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                              decoration: BoxDecoration(
                                color: AppColors.panelBg,
                                border: Border.all(color: AppColors.outputBorder, width: 1.5),
                                borderRadius:
                                    const BorderRadius.vertical(bottom: Radius.circular(10)),
                              ),
                              child: SingleChildScrollView(
                                reverse: true,
                                child: Text(
                                  _output.isEmpty ? '' : _output,
                                  style: GoogleFonts.inter(
                                    fontSize: 40,
                                    color: const Color(0xFF1A1A2E),
                                    fontWeight: FontWeight.w500,
                                  ),
                                ),
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 7),
                        // ── QuickWordsPanel ───────────────────────────────────
                        Builder(
                          builder: (context) {
                            Widget buildPredictionRow(List<Map<String, String>> words, {bool showAiIcon = false}) {
                              return Row(
                                children: [
                                  if (showAiIcon) ...[
                                    Container(
                                      width: 30,
                                      height: 90,
                                      margin: const EdgeInsets.only(right: 6),
                                      decoration: BoxDecoration(
                                        color: AppColors.accentCyan.withOpacity(0.12),
                                        borderRadius: BorderRadius.circular(8),
                                      ),
                                      child: const Icon(
                                        Icons.auto_awesome,
                                        color: AppColors.accentCyan,
                                        size: 16,
                                      ),
                                    ),
                                  ],
                                  ...words.asMap().entries.map((e) {
                                    final wordMap = e.value;
                                    final wordText = wordMap['word']!;
                                    final isAi = wordMap['source'] == 'ai';
                                    return Expanded(
                                      child: Padding(
                                        padding: EdgeInsets.only(left: e.key == 0 && !showAiIcon ? 0 : e.key == 0 ? 0 : 4),
                                        child: DwellWordChip(
                                          label: wordText,
                                          onTap: () => _acceptPrediction(wordText),
                                          isPrediction: true,
                                          isTopPick: isAi,
                                        ),
                                      ),
                                    );
                                  }),
                                ],
                              );
                            }

                            Widget buildStaticRow(List<String> words) {
                              return Row(
                                children: words.asMap().entries.map((e) {
                                  return Expanded(
                                    child: Padding(
                                      padding: EdgeInsets.only(left: e.key == 0 ? 0 : 4),
                                      child: DwellWordChip(
                                        label: e.value,
                                        onTap: () => _addWord(e.value),
                                      ),
                                    ),
                                  );
                                }).toList(),
                              );
                            }

                            if (_predictions.isNotEmpty) {
                              final midpoint = (_predictions.length / 2).ceil().clamp(1, _predictions.length);
                              final row1 = _predictions.sublist(0, midpoint);
                              final row2 = _predictions.length > midpoint
                                  ? _predictions.sublist(midpoint)
                                  : <Map<String, String>>[];
                              return SizedBox(
                                height: 185,
                                child: Column(
                                  children: [
                                    buildPredictionRow(row1, showAiIcon: true),
                                    const SizedBox(height: 5),
                                    row2.isNotEmpty
                                        ? buildPredictionRow(row2, showAiIcon: false)
                                        : const SizedBox(height: 90),
                                  ],
                                ),
                              );
                            } else {
                              return SizedBox(
                                height: 185,
                                child: Column(
                                  children: [
                                    buildStaticRow(AppConstants.quickWordsRow1),
                                    const SizedBox(height: 5),
                                    buildStaticRow(AppConstants.quickWordsRow2),
                                  ],
                                ),
                              );
                            }
                          },
                        ),
                        const SizedBox(height: 7),
                        Expanded(
                          child: GazeKeyboard(
                            isUpperCase: _isUpperCase,
                            isNumbers: _isNumbers,
                            addChar: _addChar,
                            toggleShift: _toggleShift,
                            toggleNumbers: _toggleNumbers,
                            backspace: _backspace,
                            deleteLastWord: _deleteLastWord,
                            addSpace: _addSpace,
                            newLine: _newLine,
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 12),
                  // Right: sentences + functions
                  SizedBox(
                    width: 348,
                    child: Column(
                      children: [
                        Expanded(
                          // ── SentencesPanel ────────────────────────────────────
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              // Header
                              Container(
                                padding: const EdgeInsets.symmetric(vertical: 10),
                                decoration: const BoxDecoration(
                                  color: AppColors.panelHeader,
                                  borderRadius: BorderRadius.vertical(top: Radius.circular(10)),
                                ),
                                child: Row(
                                  mainAxisAlignment: MainAxisAlignment.center,
                                  children: [
                                    Text(
                                      'Sentences',
                                      style: GoogleFonts.inter(
                                          color: Colors.white, fontSize: 15, fontWeight: FontWeight.w700),
                                    ),
                                    if (_sentencePredictions.isNotEmpty) ...[
                                      const SizedBox(width: 6),
                                      Icon(Icons.auto_awesome, color: AppColors.accentCyan, size: 14),
                                    ],
                                  ],
                                ),
                              ),
                              // List
                              Expanded(
                                child: Container(
                                  decoration: BoxDecoration(
                                    color: AppColors.panelBg,
                                    border: Border.all(color: AppColors.panelBorder, width: 1.2),
                                    borderRadius:
                                        const BorderRadius.vertical(bottom: Radius.circular(10)),
                                  ),
                                  child: ListView.separated(
                                    padding: const EdgeInsets.symmetric(vertical: 4),
                                    itemCount: _sentencePredictions.length + AppConstants.presetSentences.length,
                                    separatorBuilder: (_, i) {
                                      if (i == _sentencePredictions.length - 1 && _sentencePredictions.isNotEmpty) {
                                        return Divider(
                                          height: 2,
                                          thickness: 2,
                                          color: AppColors.accentCyan.withOpacity(0.3),
                                        );
                                      }
                                      return const Divider(height: 1, thickness: 0.5);
                                    },
                                    itemBuilder: (_, i) {
                                      if (i < _sentencePredictions.length) {
                                        return DwellSentenceTile(
                                          text: _sentencePredictions[i],
                                          onTap: () => _addSentence(_sentencePredictions[i]),
                                          isAiPrediction: true,
                                        );
                                      }
                                      final presetIdx = i - _sentencePredictions.length;
                                      return DwellSentenceTile(
                                        text: AppConstants.presetSentences[presetIdx],
                                        onTap: () => _addSentence(AppConstants.presetSentences[presetIdx]),
                                      );
                                    },
                                  ),
                                ),
                              ),
                            ],
                          ),
                        ),
                        const SizedBox(height: 20),
                        // ── FunctionsPanel ────────────────────────────────────
                        Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            // Header
                            Container(
                              height: 40,
                              decoration: BoxDecoration(
                                color: AppColors.panelHeader,
                                borderRadius: BorderRadius.circular(8),
                              ),
                              child: Stack(
                                alignment: Alignment.center,
                                children: [
                                  Text(
                                    'Other Functions',
                                    textAlign: TextAlign.center,
                                    style: GoogleFonts.inter(
                                        color: Colors.white, fontSize: 14, fontWeight: FontWeight.w700),
                                  ),
                                  Positioned(
                                    right: 2,
                                    child: IconButton(
                                      icon: const Icon(Icons.settings, color: Colors.white70, size: 18),
                                      onPressed: () => context.push('/settings'),
                                      padding: EdgeInsets.zero,
                                      constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
                                    ),
                                  ),
                                ],
                              ),
                            ),
                            const SizedBox(height: 8),
                            buildDwellFuncBtn(
                              label: 'Speak',
                              icon: Icons.volume_up_rounded,
                              color: AppColors.speakBtn,
                              onTap: () => _speak(_output),
                              height: 130,
                            ),
                            const SizedBox(height: 8),
                            buildDwellFuncBtn(
                              label: 'Clear',
                              icon: Icons.delete_outline_rounded,
                              color: AppColors.clearBtn,
                              onTap: () {
                                _clear();
                              },
                              height: 110,
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
