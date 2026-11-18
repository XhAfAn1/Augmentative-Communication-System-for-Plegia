/// Sentence suggestions that always fit what is typed (no network code here, so it is easy to test).
///
/// * The 3B model is asked at word boundaries (as before); while letters are typed, its last list is
///   FILTERED by the letters instead of staying on screen unchanged, and the profile's phrase memory (a cheap
///   lookup, no model) is asked at every letter. The model is asked again (after a short pause) when fewer
///   than [minShown] sentences still fit; sentences that still fit keep their place, new ones come after.
/// * Outdated replies are ignored: every request gets a number, only the newest request's reply is used
///   (a slow reply for older text can no longer overwrite the current suggestions).
class SentenceSuggestions {
  static const int minShown = 3; // fewer fitting sentences than this -> ask the model for more
  List<String> _model = []; // last model reply (may be for a shorter, earlier text: filtered when shown)
  List<String> _phrases = []; // phrase memory matches (shown first)
  int _modelSeq = 0;
  int _phraseSeq = 0;

  int nextModelRequest() => ++_modelSeq;
  int nextPhraseRequest() => ++_phraseSeq;

  /// Uses a model reply only if it answers the newest model request. With [typed], the earlier sentences
  /// that still fit stay first (same place on screen) and the new ones are added after them.
  bool acceptModel(int seq, List<String> list, {String? typed}) {
    if (seq != _modelSeq) return false;
    _model = [if (typed != null) ..._model.where((s) => fits(s, typed)), ...list];
    return true;
  }

  /// Uses a phrase-memory reply only if it answers the newest phrase request.
  bool acceptPhrases(int seq, List<String> list) {
    if (seq != _phraseSeq) return false;
    _phrases = List.of(list);
    return true;
  }

  void clear() {
    _model = [];
    _phrases = [];
    _modelSeq++; // replies still on their way belong to the old text
    _phraseSeq++;
  }

  /// What to show for the typed text: phrase memory first, then the model's sentences; only sentences that
  /// start with the typed text (and are longer), no duplicates, at most [max].
  List<String> visible(String typed, {int max = 5}) {
    if (typed.trim().isEmpty) return const [];
    final out = <String>[];
    final seen = <String>{};
    for (final s in [..._phrases, ..._model]) {
      if (out.length >= max) break;
      if (fits(s, typed) && seen.add(norm(s))) out.add(s);
    }
    return out;
  }

  static String norm(String s) =>
      s.toLowerCase().replaceAll(RegExp(r"[^\w' ]"), ' ').replaceAll(RegExp(r'\s+'), ' ').trim();

  /// True if [sentence] continues [typed]. A typed word that ends with a space must be complete:
  /// "i need " fits "I need water." but not "I needed water.".
  static bool fits(String sentence, String typed) {
    var t = norm(typed);
    if (t.isEmpty) return false;
    if (RegExp(r'\s$').hasMatch(typed)) t += ' ';
    final s = norm(sentence);
    return s.startsWith(t) && s.length > t.trim().length;
  }
}
