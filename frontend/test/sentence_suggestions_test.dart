// Sentence suggestions keep fitting what is typed, and outdated (slow) model replies are ignored.
import 'package:flutter_test/flutter_test.dart';
import 'package:synaptalk/features/keyboard/sentence_suggestions.dart';

void main() {
  test('shown sentences follow the typed letters', () {
    final s = SentenceSuggestions();
    s.acceptModel(s.nextModelRequest(), ['I need water please.', 'I need my medicine.', 'I need to rest.']);
    expect(s.visible('i need '), ['I need water please.', 'I need my medicine.', 'I need to rest.']);
    expect(s.visible('i need w'), ['I need water please.']); // before: all 3 stayed on screen
    expect(s.visible('i need x'), isEmpty); // nothing fits -> the app asks the model again
    expect(s.visible('i need'), hasLength(3)); // backspace: they come back
    expect(s.visible(''), isEmpty);
  });

  test('phrase memory first, no duplicates, at most 5', () {
    final s = SentenceSuggestions();
    s.acceptModel(s.nextModelRequest(), ['I need to rest.', 'I need my medicine', 'I need a', 'I need b', 'I need c']);
    s.acceptPhrases(s.nextPhraseRequest(), ['I need my medicine.', 'I need my glasses.']);
    final v = s.visible('i need ');
    expect(v.first, 'I need my medicine.');
    expect(v[1], 'I need my glasses.');
    expect(v.where((x) => SentenceSuggestions.norm(x) == 'i need my medicine'), hasLength(1));
    expect(v, hasLength(5));
  });

  test('outdated replies are ignored', () {
    final s = SentenceSuggestions();
    final old = s.nextModelRequest(); // asked for "i want"
    final now = s.nextModelRequest(); // asked for "i need"
    expect(s.acceptModel(now, ['I need help.']), isTrue);
    expect(s.acceptModel(old, ['I want to sleep.']), isFalse); // the slow old reply arrives last
    expect(s.visible('i need '), ['I need help.']);
    final p1 = s.nextPhraseRequest(), p2 = s.nextPhraseRequest();
    expect(s.acceptPhrases(p1, ['I need water.']), isFalse);
    expect(s.acceptPhrases(p2, ['I need my glasses.']), isTrue);
  });

  test('clear forgets lists and replies still on their way', () {
    final s = SentenceSuggestions();
    final pending = s.nextModelRequest();
    s.clear();
    expect(s.acceptModel(pending, ['I need help.']), isFalse);
    expect(s.visible('i need '), isEmpty);
  });

  test('a finished word must match completely', () {
    expect(SentenceSuggestions.fits('I needed water.', 'i need '), isFalse);
    expect(SentenceSuggestions.fits('I needed water.', 'i need'), isTrue);
    expect(SentenceSuggestions.fits('I need water.', 'I NEED WAT'), isTrue);
    expect(SentenceSuggestions.fits('I need water.', 'i need water'), isFalse); // nothing left to add
  });

  test('refill keeps the sentences that still fit in place, new ones after them', () {
    final s = SentenceSuggestions();
    s.acceptModel(s.nextModelRequest(), ['I need water please.', 'I need my medicine.', 'I need wool socks.']);
    expect(s.visible('i need wa'), ['I need water please.']); // 1 left (< minShown) -> the app asks for more
    s.acceptModel(s.nextModelRequest(), ['I need water now.', 'I need water please.', 'I need a walk.'], typed: 'i need wa');
    expect(s.visible('i need wa'), ['I need water please.', 'I need water now.']); // kept first, no duplicate
    expect(SentenceSuggestions.minShown, 3);
  });

  test('suggestions are for the sentence being typed, finished sentences stay', () {
    String current(String t) => t.substring(SentenceSuggestions.sentenceStart(t));
    String done(String t) => t.substring(0, SentenceSuggestions.sentenceStart(t));
    expect(current('I am hungry. Can'), 'Can');
    expect(done('I am hungry. Can'), 'I am hungry. ');
    expect(current('Help me? I need '), 'I need ');
    expect(current('Hello, I need'), 'Hello, I need'); // a comma does not end a sentence
    expect(current('Hello '), 'Hello '); // no sentence ended yet: whole text (as before)
    expect(current('I am hungry. '), ''); // new sentence not started: nothing to suggest for
    expect(current('Hi\nCan'), 'Can'); // new line also starts a new sentence (as before)
    // picking "Can you help me?" after "I am hungry. Can" keeps the first sentence
    expect('${done('I am hungry. Can')}Can you help me? ', 'I am hungry. Can you help me? ');
    final s = SentenceSuggestions();
    s.acceptModel(s.nextModelRequest(), ['Can you help me?', 'Can I have water?']);
    expect(s.visible(current('I am hungry. Can y')), ['Can you help me?']);
  });
}
