import collections
import re
import json
import os
import math
import pickle
from llama_cpp import Llama

# ---------------------------------------------------------
# 1. FIXED & NORMALIZED FREQUENCY TRIE (Unigrams)
# ---------------------------------------------------------
class TrieNode:
    def __init__(self):
        self.children = {}
        self.is_end_of_word = False
        self.frequency = 0

class FrequencyTrie:
    def __init__(self):
        self.root = TrieNode()
        self.top_global_words = []
        self._all_words = []

    def insert(self, word: str, frequency: int):
        word = word.lower().strip()
        if not word: return
        node = self.root
        for char in word:
            if char not in node.children: node.children[char] = TrieNode()
            node = node.children[char]
        node.is_end_of_word = True
        node.frequency = max(node.frequency, frequency)
        self._all_words.append((word, frequency))

    def update_frequency(self, word: str, increment: int = 5):
        word = word.lower().strip()
        if not word: return
        node = self.root
        for char in word:
            if char not in node.children: node.children[char] = TrieNode()
            node = node.children[char]
        node.is_end_of_word = True
        node.frequency += increment

    def build_cache(self):
        self._all_words.sort(key=lambda x: x[1], reverse=True)
        seen = set()
        self.top_global_words = []
        for w, f in self._all_words:
            if w not in seen:
                seen.add(w)
                self.top_global_words.append(w)
                if len(self.top_global_words) >= 10: break
        self._all_words = []

    def is_valid_word(self, word: str) -> bool:
        word = word.lower().strip()
        node = self.root
        for char in word:
            if char not in node.children: return False
            node = node.children[char]
        return node.is_end_of_word

    def get_words_with_prefix(self, prefix: str, top_n: int = 10):
        prefix = prefix.lower().strip()
        if not prefix: return self.top_global_words[:top_n]
        node = self.root
        for char in prefix:
            if char not in node.children: return []
            node = node.children[char]

        results = []
        def dfs(curr_node, curr_prefix):
            if curr_node.is_end_of_word:
                results.append((curr_prefix, curr_node.frequency))
            for char, child_node in curr_node.children.items():
                dfs(child_node, curr_prefix + char)

        dfs(node, prefix)
        results.sort(key=lambda x: x[1], reverse=True)
        return [word for word, freq in results[:top_n]]

# ---------------------------------------------------------
# 2. CONTEXT MANAGER & N-GRAM PREDICTORS
# ---------------------------------------------------------
class ContextManager:
    @staticmethod
    def get_context(text: str, n: int = 2) -> str:
        """Extracts the last N words from the text to form a context key."""
        words = re.findall(r"\b[a-zA-Z]+(?:'[a-zA-Z]+)?\b", text.lower())
        if len(words) == 0:
            return ""
        return " ".join(words[-n:])

class BaseNgramEngine:
    def __init__(self):
        # Format: {"word1 word2": {"next_word": 10}}
        self.ngrams = collections.defaultdict(collections.Counter)

    def update(self, context: str, next_word: str, increment: int = 1):
        if not context or not next_word:
            return
        self.ngrams[context][next_word] += increment

    def get_scores(self, context: str) -> dict:
        """Returns a normalized [0,1] dictionary of next words for a context."""
        if context not in self.ngrams or not self.ngrams[context]:
            return {}

        counts = self.ngrams[context]
        max_count = max(counts.values())
        if max_count == 0:
            return {}

        return {word: count / max_count for word, count in counts.items()}

    def get_total_confidence(self, context: str) -> int:
        """Returns the total number of observations for this context."""
        if context not in self.ngrams:
            return 0
        return sum(self.ngrams[context].values())

class PersonalizationEngine(BaseNgramEngine):
    """Tracks user-specific frequently typed phrases."""
    pass

class TrigramPredictor(BaseNgramEngine):
    """Global N-gram model loaded from pre-trained trigrams.json."""
    pass

# ---------------------------------------------------------
# 3. RANKING ENGINE (Dynamic Weights)
# ---------------------------------------------------------
class RankingEngine:
    @staticmethod
    def rank_candidates(
        pers_scores: dict,
        trigram_scores: dict,
        llm_scores: dict,
        ngram_total_confidence: int,
        word_count: int = 1,
        top_n: int = 5
    ):
        """
        Combines scores using DYNAMIC weighting based on n-gram evidence strength
        AND sentence length. Longer sentences = more LLM weight (it understands
        long-range context better than n-grams).

        FIX 1: LLM floor raised from 0.15 -> 0.40 so it always has real influence.
        FIX 3: N-gram top-10 candidates are re-scored by LLM agreement before merge.
        FIX 4: Word count scaling — every word beyond 2 adds +3% LLM weight.
        """
        all_words = set(pers_scores.keys()) | set(trigram_scores.keys()) | set(llm_scores.keys())

        # Compute n-gram combined scores (best of personalization + trigram)
        ngram_combined = {}
        for word in set(pers_scores.keys()) | set(trigram_scores.keys()):
            p = pers_scores.get(word, 0.0)
            t = trigram_scores.get(word, 0.0)
            ngram_combined[word] = max(p, t)

        # FIX 3: Re-score n-gram candidates by their LLM agreement
        # Words that both n-gram AND LLM agree on get a significant boost.
        # Words that only n-gram likes but LLM ignores get a mild penalty.
        if llm_scores:
            for word in list(ngram_combined.keys()):
                llm_agreement = llm_scores.get(word, 0.0)
                # Boost by up to 50% if LLM agrees; penalize by 20% if LLM completely ignores it
                if llm_agreement > 0.5:
                    ngram_combined[word] = min(1.0, ngram_combined[word] * 1.5)
                elif llm_agreement == 0.0:
                    ngram_combined[word] = ngram_combined[word] * 0.8

        # FIX 1 + FIX 4: Dynamic weight with raised floor (0.40) + length scaling.
        # - Base LLM floor: 0.40 (was 0.15) — LLM always has meaningful influence
        # - Length bonus: +3% per word beyond 2 words (up to +20% extra)
        # - High n-gram confidence reduces LLM share, but never below the floor
        base_llm_floor = 0.40
        length_bonus = min(0.20, max(0, word_count - 2) * 0.03)
        llm_floor = min(0.75, base_llm_floor + length_bonus)
        llm_weight = max(llm_floor, 1.0 / (1.0 + ngram_total_confidence * 0.05))
        ngram_weight = 1.0 - llm_weight

        final_scores = {}
        for word in all_words:
            ng = ngram_combined.get(word, 0.0)
            l = llm_scores.get(word, 0.0)
            final_scores[word] = ngram_weight * ng + llm_weight * l

        sorted_words = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
        return [word for word, score in sorted_words[:top_n]]

# ---------------------------------------------------------
# 4. INTELLECTUAL HYBRID ORCHESTRATOR (Upgraded)
# ---------------------------------------------------------
class HybridKeyboardBackend:
    def __init__(self, fast_gguf_path: str, heavy_gguf_path: str, vocab_path: str):
        self.trie = FrequencyTrie()
        self.personalization = PersonalizationEngine()
        self.trigram = TrigramPredictor()

        # ACTION 3: Session history buffer (last 5 completed sentences)
        self.session_history = collections.deque(maxlen=5)

        # Path for persisting personalization data
        self._pers_path = os.path.join(os.path.dirname(fast_gguf_path), "personalization.pkl")

        if os.path.exists(vocab_path):
            with open(vocab_path, 'r') as f:
                base_vocab = json.load(f)
            for word, freq in base_vocab.items():
                self.trie.insert(word, freq)
            self.trie.build_cache()
        else:
            print(f"Warning: {vocab_path} not found. Trie will be empty.")

        # Load pre-trained trigrams
        trigrams_path = "trigrams.json"
        if os.path.exists(trigrams_path):
            print(f"[Backend] Loading pre-trained trigrams from {trigrams_path}...")
            try:
                with open(trigrams_path, 'r', encoding='utf-8') as f:
                    pretrained = json.load(f)
                for context, next_words in pretrained.items():
                    for word, count in next_words.items():
                        self.trigram.update(context, word, increment=count)
                print(f"[Backend] Loaded {len(pretrained)} contexts into TrigramPredictor.")
            except Exception as e:
                print(f"Error loading trigrams.json: {e}")
        else:
            print(f"Warning: {trigrams_path} not found. TrigramPredictor will start empty.")

        # ACTION 6: Load persisted personalization data from previous sessions
        if os.path.exists(self._pers_path):
            try:
                with open(self._pers_path, 'rb') as f:
                    self.personalization = pickle.load(f)
                print(f"[Backend] Loaded persisted personalization data.")
            except Exception as e:
                print(f"[Backend] Could not load personalization data: {e}. Starting fresh.")

        # Initialize the dual LLM models
        print(f"[Backend] Loading Fast LLM from {fast_gguf_path}...")
        self.fast_llm = Llama(model_path=fast_gguf_path, n_ctx=512, logits_all=True, verbose=False)
        print(f"[Backend] Loading Heavy LLM from {heavy_gguf_path}...")
        self.heavy_llm = Llama(model_path=heavy_gguf_path, n_ctx=512, verbose=False)
        self.ai_cache = {}

        # Note: Smart Compose now uses purely the Llama 3 LLM (no static dataset).

    # ---------------------------------------------------------
    # INTERNAL: Build context-aware LLM prompt (Action 1)
    # ---------------------------------------------------------
    def _build_llm_prompt(self, current_sentence: str) -> str:
        """
        FIX 2: Replaced AAC-specific persona with a neutral smart-keyboard framing.
        The old 'AAC user' persona caused the LLM to bleed in medical vocabulary.
        Now uses a neutral, domain-free framing that works for any conversation topic.
        Session history is preserved to give the LLM continuity of topic.
        """
        history_str = ""
        if self.session_history:
            history_str = "Context from earlier in this conversation:\n"
            for s in self.session_history:
                history_str += f"- {s}\n"
            history_str += "\n"

        prompt = (
            "You are a smart keyboard next-word predictor. "
            "Complete the sentence below with the single most natural and contextually "
            "appropriate next word. Output only the word, nothing else.\n\n"
            f"{history_str}"
            f"Sentence: {current_sentence}"
        )
        return prompt

    # ---------------------------------------------------------
    # INTERNAL: Get LLM scores (Actions 1, 5)
    # ---------------------------------------------------------
    def _get_llm_scores(self, current_sentence: str) -> dict:
        """
        Always runs the LLM with a context-aware prompt and expanded logprobs.
        Returns a normalized {word: score} dict.
        """
        prompt = self._build_llm_prompt(current_sentence)

        try:
            output = self.fast_llm.create_completion(
                prompt=prompt,
                max_tokens=1,
                logprobs=200,       # ACTION 5: was 50, now 200 for broader candidates
                echo=False,
                temperature=0,      # Greedy, deterministic — best single prediction
            )
        except Exception as e:
            print(f"[LLM] Error during completion: {e}")
            return {}

        top_tokens = output["choices"][0]["logprobs"]["top_logprobs"][0]
        self.ai_cache = {}

        raw_scores = {}
        for token_str, logprob in top_tokens.items():
            valid_word = self.clean_and_validate_token(token_str)
            if valid_word:
                prob = math.exp(logprob)
                if valid_word not in raw_scores or raw_scores[valid_word] < prob:
                    raw_scores[valid_word] = prob
                if valid_word not in self.ai_cache:
                    self.ai_cache[valid_word] = logprob

        if not raw_scores:
            return {}

        max_score = max(raw_scores.values())
        if max_score == 0:
            return {}

        return {w: s / max_score for w, s in raw_scores.items()}

    # ---------------------------------------------------------
    # INTERNAL: N-gram scores with Katz Backoff (Action 4)
    # ---------------------------------------------------------
    def _get_ngram_scores_with_backoff(self, text: str) -> tuple[dict, dict, int]:
        """
        Gets n-gram scores using Katz Backoff:
        Tries 2-word context → 1-word context → global unigram top words.
        Returns (pers_scores, trigram_scores, total_confidence).
        """
        # --- Try 2-word (trigram) context ---
        ctx2 = ContextManager.get_context(text, n=2)
        pers_scores_2 = self.personalization.get_scores(ctx2)
        trigram_scores_2 = self.trigram.get_scores(ctx2)
        conf_2 = (
            self.personalization.get_total_confidence(ctx2) +
            self.trigram.get_total_confidence(ctx2)
        )

        # --- Try 1-word (bigram) context ---
        ctx1 = ContextManager.get_context(text, n=1)
        pers_scores_1 = self.personalization.get_scores(ctx1)
        trigram_scores_1 = self.trigram.get_scores(ctx1)
        conf_1 = (
            self.personalization.get_total_confidence(ctx1) +
            self.trigram.get_total_confidence(ctx1)
        )

        # Merge: 2-word scores get full weight, 1-word scores fill gaps with 0.7x weight
        merged_pers = {}
        merged_trigram = {}

        for word, score in pers_scores_1.items():
            merged_pers[word] = score * 0.7

        for word, score in trigram_scores_1.items():
            merged_trigram[word] = score * 0.7

        # 2-gram scores override 1-gram scores where they exist
        for word, score in pers_scores_2.items():
            merged_pers[word] = score

        for word, score in trigram_scores_2.items():
            merged_trigram[word] = score

        # If still completely empty, fall back to global unigram top words
        total_conf = conf_2 + conf_1
        if not merged_pers and not merged_trigram:
            fallback = {w: 1.0 / (i + 1) for i, w in enumerate(self.trie.top_global_words)}
            return fallback, fallback, 0

        return merged_pers, merged_trigram, total_conf

    # ---------------------------------------------------------
    # TOKEN VALIDATION
    # ---------------------------------------------------------
    def clean_and_validate_token(self, token_str: str):
        clean = token_str.replace("Ġ", "").replace(" ", "").strip().lower()
        clean = re.sub(r"^[^\w]+|[^\w]+$", "", clean)
        if not re.match(r"^[a-z]+(?:'[a-z]+)?$", clean):
            return None
        if self.trie.is_valid_word(clean) or len(clean) >= 4:
            return clean
        return None

    # ---------------------------------------------------------
    # PUBLIC: On Spacebar Fast — Instant N-Gram Prediction
    # ---------------------------------------------------------
    def on_spacebar_fast(self, current_sentence: str):
        """
        Instant statistical prediction using only N-grams and the Trie.
        Returns in < 1ms to give the user the illusion of zero latency
        while the heavy LLM thinks in the background.
        """
        if not current_sentence.strip():
            return []

        # Get n-gram scores with Katz Backoff
        pers_scores, trigram_scores, _ = self._get_ngram_scores_with_backoff(current_sentence)

        # Merge and sort
        all_words = set(pers_scores.keys()) | set(trigram_scores.keys())
        final_scores = {}
        for word in all_words:
            p = pers_scores.get(word, 0.0)
            t = trigram_scores.get(word, 0.0)
            final_scores[word] = max(p, t)

        sorted_words = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
        top_words = [w for w, s in sorted_words[:5]]

        results = [{"word": w, "source": "trie"} for w in top_words]
        return results

    # ---------------------------------------------------------
    # PUBLIC: On Spacebar AI — Heavy Hybrid Prediction
    # ---------------------------------------------------------
    def on_spacebar(self, current_sentence: str):
        """
        Triggered at word boundaries. Now used asynchronously by the UI.
        Runs the heavy Llama 3B model and perfectly blends the scores.
        Takes ~250ms.
        """
        if not current_sentence.strip():
            return []

        # Get n-gram scores with Katz Backoff
        pers_scores, trigram_scores, total_ngram_conf = self._get_ngram_scores_with_backoff(current_sentence)

        # Always run LLM with rich context prompt (FIX 2: neutral prompt)
        llm_scores = self._get_llm_scores(current_sentence)

        # FIX 4: Compute word count to scale LLM weight with sentence length
        word_count = len(re.findall(r"\b[a-zA-Z]+\b", current_sentence))

        # Compute effective LLM weight for logging
        base_llm_floor = 0.40
        length_bonus = min(0.20, max(0, word_count - 2) * 0.03)
        llm_floor = min(0.75, base_llm_floor + length_bonus)
        effective_llm_w = max(llm_floor, 1.0 / (1.0 + total_ngram_conf * 0.05))
        print(f"[Engine] words={word_count}, ngram_conf={total_ngram_conf}, LLM_weight={effective_llm_w:.2f}")

        # Dynamic ranking with FIX 1 (raised floor), FIX 3 (re-ranking), FIX 4 (length scaling)
        top_words = RankingEngine.rank_candidates(
            pers_scores,
            trigram_scores,
            llm_scores,
            ngram_total_confidence=total_ngram_conf,
            word_count=word_count,
            top_n=10
        )

        # Map source for UI
        results = []
        for w in top_words:
            if w in pers_scores or w in trigram_scores:
                results.append({"word": w, "source": "trie"})
            else:
                results.append({"word": w, "source": "ai"})

        return results

    # ---------------------------------------------------------
    # PUBLIC: On Sentence Submitted (Action 3)
    # ---------------------------------------------------------
    def on_sentence_submitted(self, sentence: str):
        """
        Called when the user finishes a sentence (hits enter or submits).
        Adds the sentence to the session history buffer and triggers learning.
        """
        sentence = sentence.strip()
        if sentence:
            self.session_history.append(sentence)
            print(f"[Backend] Session history updated. Buffer size: {len(self.session_history)}")
            self.learn_user_typing(sentence)

    # ---------------------------------------------------------
    # PUBLIC: Smart Compose / Sentence Completion
    # ---------------------------------------------------------
    def on_sentence_completion(self, current_text: str, count: int = 5):
        """Generates full-sentence completions based on the typed text."""
        clean_input = current_text.lower().strip()
        if not clean_input:
            return []

        # Query the Heavy LLM for intelligent sentence completions
        matches = []
        try:
            history_str = ""
            if self.session_history:
                history_str = "\n".join(f"- {s}" for s in self.session_history)
                history_str = f"Prior context:\n{history_str}\n\n"

            messages = [
                {
                    "role": "system",
                    "content": "You are a smart keyboard assistant that helps complete user sentences. Your job is to output exactly 5 different natural, grammatically correct ways to complete the sentence provided by the user. Do not add any introductory text. Just output a numbered list from 1 to 5. Ensure each sentence starts with the user's provided text."
                },
                {
                    "role": "user",
                    "content": f"{history_str}Provide 5 completions for this text:\n{current_text}"
                }
            ]

            output = self.heavy_llm.create_chat_completion(
                messages=messages,
                max_tokens=150,
                temperature=0.5,
            )
            text = output["choices"][0]["message"]["content"].strip()

            lines = text.split('\n')
            for line in lines:
                match = re.match(r'^\d+\.\s*(.*)', line.strip())
                if match:
                    full_sentence = match.group(1).strip()
                    full_sentence = re.sub(r'\s+([.,?!])', r'\1', full_sentence)
                    if full_sentence and full_sentence[-1] not in ['.', '?', '!']:
                        full_sentence += "."
                    if len(full_sentence) > 0:
                        full_sentence = full_sentence[0].upper() + full_sentence[1:]
                    if "_" in full_sentence or ".." in full_sentence or "()" in full_sentence.replace(" ", "") or "（）" in full_sentence:
                        continue
                    if len(full_sentence) > len(current_text) and full_sentence.lower().startswith(clean_input) and full_sentence not in matches:
                        matches.append(full_sentence)
                        if len(matches) >= count:
                            break
        except Exception as e:
            print(f"Error in LLM sentence completion: {e}", flush=True)

        return matches[:count]

    # ---------------------------------------------------------
    # PUBLIC: On Letter Typed — Prefix Prediction
    # ---------------------------------------------------------
    def on_letter_typed(self, prefix: str):
        """Triggered on instant keystrokes to return low-latency prefix predictions."""
        prefix = prefix.lower().strip()
        trie_matches = self.trie.get_words_with_prefix(prefix, top_n=10)
        ai_matches = [w for w in self.ai_cache if w.startswith(prefix)]
        ai_matches.sort(key=lambda w: self.ai_cache[w], reverse=True)

        final_suggestions = []
        seen = set()

        for w in ai_matches:
            if w not in seen:
                seen.add(w)
                final_suggestions.append({"word": w, "source": "ai"})

        for w in trie_matches:
            if w not in seen:
                seen.add(w)
                final_suggestions.append({"word": w, "source": "trie"})

        return final_suggestions[:10]

    # ---------------------------------------------------------
    # PUBLIC: Learn User Typing (Action 6 — Fixed)
    # ---------------------------------------------------------
    def learn_user_typing(self, full_sentence: str):
        """
        Dynamically tracks user behavior and updates n-gram frequencies.
        - ACTION 6: increment reduced from 100 → 5 for the trie (less aggressive)
        - ACTION 6: learns from ALL word boundaries, not just the last word
        - ACTION 6: persists personalization state to disk
        """
        words = re.findall(r"\b[a-zA-Z]+(?:'[a-zA-Z]+)?\b", full_sentence.lower())
        if not words:
            return

        # Learn from every word position in the sentence
        for i in range(len(words)):
            word = words[i]

            # Update unigram trie frequency (reduced from 100 to 5)
            if word.isalpha():
                self.trie.update_frequency(word, increment=5)

            # Update bigram (1-word context)
            if i >= 1:
                context_1 = words[i - 1]
                self.personalization.update(context_1, word, increment=1)

            # Update trigram (2-word context)
            if i >= 2:
                context_2 = f"{words[i - 2]} {words[i - 1]}"
                self.personalization.update(context_2, word, increment=1)

        # ACTION 6: Persist personalization to disk so it survives restarts
        self._save_personalization()

    # ---------------------------------------------------------
    # INTERNAL: Save Personalization to Disk
    # ---------------------------------------------------------
    def _save_personalization(self):
        """Saves personalization engine state to disk."""
        try:
            with open(self._pers_path, 'wb') as f:
                pickle.dump(self.personalization, f)
        except Exception as e:
            print(f"[Backend] Warning: Could not save personalization data: {e}")


if __name__ == "__main__":
    print("HybridKeyboardBackend module loaded successfully.")
