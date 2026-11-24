import collections
import re
import json
import os
import math
import time
import bisect
import numpy as np
from llama_cpp import Llama

# ---------------------------------------------------------
# 1. FIXED & NORMALIZED FREQUENCY TRIE (Unigrams)
# ---------------------------------------------------------
class FrequencyTrie:
    """vocab.json word list for prefix completion (name kept from the old trie; same results, same interface).
    Was a tree of Python objects (~99 MB, 18 ms for a 1-letter prefix); now one alphabetical list + bisect (~14 MB,
    < 1 ms). Order = highest frequency first; equal frequencies (most of the 158k words share one) keep the old
    trie's depth-first order, so every prefix gives exactly the old list (checked on 12,466 prefixes,
    research/tests_20261123). Call insert() for each word, then build_cache() once."""

    def __init__(self):
        self.top_global_words = []
        self._all_words = []          # (word, frequency) as inserted, until build_cache()
        self._alpha = []              # all words, alphabetical
        self._freq = np.zeros(0, dtype=np.int64)
        self._rank = np.zeros(0, dtype=np.int32)   # old trie depth-first position: breaks frequency ties
        self._cache = {}

    def insert(self, word: str, frequency: int):
        word = word.lower().strip()
        if not word: return
        self._all_words.append((word, frequency))

    def build_cache(self):
        freq, words = {}, []                     # first insertion order; a repeated word keeps its highest count
        for w, f in self._all_words:
            if w in freq:
                freq[w] = max(freq[w], f)
            else:
                freq[w] = f
                words.append(w)
        # trie depth-first order: at each letter, branches in the order they were first inserted
        first = {}
        for i, w in enumerate(words):
            for k in range(1, len(w) + 1):
                first.setdefault(w[:k], i)
        dfs = sorted(words, key=lambda w: tuple(first[w[:k]] for k in range(1, len(w) + 1)))
        rank = {w: i for i, w in enumerate(dfs)}
        del first, dfs
        self._alpha = sorted(words)
        self._freq = np.array([freq[w] for w in self._alpha], dtype=np.int64)
        self._rank = np.array([rank[w] for w in self._alpha], dtype=np.int32)

        self._all_words.sort(key=lambda x: x[1], reverse=True)
        seen = set()
        self.top_global_words = []
        for w, f in self._all_words:
            if w not in seen:
                seen.add(w)
                self.top_global_words.append(w)
                if len(self.top_global_words) >= 10: break
        self._all_words = []
        self._cache.clear()

    def is_valid_word(self, word: str) -> bool:
        word = word.lower().strip()
        i = bisect.bisect_left(self._alpha, word)
        return i < len(self._alpha) and self._alpha[i] == word

    def get_words_with_prefix(self, prefix: str, top_n: int = 10):
        prefix = prefix.lower().strip()
        if not prefix: return self.top_global_words[:top_n]
        if prefix in self._cache:
            return self._cache[prefix][:top_n]
        lo = bisect.bisect_left(self._alpha, prefix)
        hi = bisect.bisect_left(self._alpha, prefix[:-1] + chr(ord(prefix[-1]) + 1))   # all words with the prefix
        top_words = []
        if hi > lo:
            idx = np.lexsort((self._rank[lo:hi], -self._freq[lo:hi]))[:top_n] + lo
            top_words = [self._alpha[i] for i in idx]
        self._cache[prefix] = top_words
        return top_words

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

_HERE = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.environ.get("AAC_PROFILES_DIR") or os.path.join(_HERE, "profiles")
DEFAULT_PROFILES = ["Test", "Patient"]
SEED_FILES = {"Patient": os.path.join(_HERE, "seed_phrases_patient.txt")}   # read once, when the profile is created


class UserProfile:
    """One person's learning, kept apart from other profiles (testing never teaches the patient profile):
    - ngrams:  their word pairs / triples (same engine as before)
    - words:   how often they used each word (also words missing from vocab.json, e.g. names)
    - phrases: every spoken sentence with use count and last use (phrase memory)
    Saved to profiles/<name>/learning.json after every spoken sentence: temp file + replace, so a crash or
    power cut can never leave a half-written file."""

    def __init__(self, name: str, folder: str = None):
        self.name = name
        self.path = os.path.join(folder or PROFILES_DIR, name, "learning.json")
        self.ngrams = PersonalizationEngine()
        self.words = collections.Counter()
        self.phrases = {}        # key -> {"text": as spoken, "count": n, "last": unix time}

    @staticmethod
    def key(text: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^\w' ]", " ", text.lower())).strip()

    def exists(self) -> bool:
        return os.path.isfile(self.path)

    def load(self) -> bool:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            return False
        for ctx, nxt in d.get("ngrams", {}).items():
            for w, c in nxt.items():
                self.ngrams.update(ctx, w, int(c))
        self.words = collections.Counter({w: int(c) for w, c in d.get("words", {}).items()})
        self.phrases = dict(d.get("phrases", {}))
        return True

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"ngrams": {c: dict(n) for c, n in self.ngrams.ngrams.items()},
                           "words": dict(self.words), "phrases": self.phrases}, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as e:
            print(f"[Profile] Warning: could not save profile '{self.name}': {e}")

    def learn(self, sentence: str, save: bool = True):
        """Learns a spoken sentence: n-grams from every word position, word counts, phrase memory."""
        words = re.findall(r"\b[a-zA-Z]+(?:'[a-zA-Z]+)?\b", sentence.lower())
        if not words:
            return
        for i, word in enumerate(words):
            self.words[word] += 1
            if i >= 1:
                self.ngrams.update(words[i - 1], word, increment=1)
            if i >= 2:
                self.ngrams.update(f"{words[i - 2]} {words[i - 1]}", word, increment=1)
        k = self.key(sentence)
        ph = self.phrases.get(k) or {"text": sentence.strip(), "count": 0, "last": 0}
        ph["text"], ph["count"], ph["last"] = sentence.strip(), ph["count"] + 1, time.time()
        self.phrases[k] = ph
        if save:
            self.save()

    def phrases_for(self, typed: str, n: int = 3) -> list:
        """Spoken sentences that start with what is typed, most used first (then most recent)."""
        t = self.key(typed)
        if not t:
            return []
        if typed[-1:].isspace():
            t += " "                               # "i need " must not match "i needed ..."
        # list(...) = a copy made in one step: a sentence being saved at the same moment cannot break the loop
        hits = [p for k, p in list(self.phrases.items()) if k.startswith(t) and len(k) > len(t.strip())]
        hits.sort(key=lambda p: (p["count"], p["last"]), reverse=True)
        return [p["text"] for p in hits[:n]]


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
    _fast_topk = False      # True after _check_fast_topk passed (set in _load_llm_models)

    def __init__(self, fast_gguf_path: str, heavy_gguf_path: str, vocab_path: str, use_gpu: bool = True):
        self.fast_gguf_path = fast_gguf_path
        self.heavy_gguf_path = heavy_gguf_path
        self.vocab_path = vocab_path
        self.use_gpu = use_gpu

        self.trie = FrequencyTrie()
        self.personalization = PersonalizationEngine()
        self.trigram = TrigramPredictor()

        # ACTION 3: Session history buffer (last 5 completed sentences)
        self.session_history = collections.deque(maxlen=5)

        self.conv_freq = collections.Counter()   # everyday-conversation word frequency (from trigrams.json)
        self._last_cands = ("", [])              # (text before the word, ranked next-word candidates)

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
                        if " " not in context:
                            self.conv_freq[word] += count
                print(f"[Backend] Loaded {len(pretrained)} contexts into TrigramPredictor.")
            except Exception as e:
                print(f"Error loading trigrams.json: {e}")
        else:
            print(f"Warning: {trigrams_path} not found. TrigramPredictor will start empty.")

        # Profiles: each person's learning is kept apart (see UserProfile)
        self.profile = None
        self.set_profile(self._read_active_profile())

        # Initialize the dual LLM models
        self.ai_cache = {}
        self._load_llm_models(use_gpu=use_gpu)

    # ---------------------------------------------------------
    # PROFILES
    # ---------------------------------------------------------
    def _active_file(self):
        return os.path.join(PROFILES_DIR, "active.txt")

    def _read_active_profile(self) -> str:
        try:
            with open(self._active_file(), "r", encoding="utf-8") as f:
                name = f.read().strip()
            return name if name in self.list_profiles() else DEFAULT_PROFILES[0]
        except Exception:
            return DEFAULT_PROFILES[0]

    def list_profiles(self) -> list:
        names = list(DEFAULT_PROFILES)
        try:
            names += sorted(d for d in os.listdir(PROFILES_DIR)
                            if os.path.isdir(os.path.join(PROFILES_DIR, d)) and d not in names)
        except Exception:
            pass
        return names

    def set_profile(self, name: str):
        """Switches the learning to this profile (created on first use; Patient is seeded from its phrase list)."""
        if name not in self.list_profiles():
            raise ValueError(f"unknown profile {name!r}")
        prof = UserProfile(name)
        if prof.exists():
            prof.load()
        else:
            seed = SEED_FILES.get(name)
            if seed and os.path.exists(seed):
                with open(seed, "r", encoding="utf-8") as f:
                    lines = [l.strip() for l in f if l.strip() and not l.lstrip().startswith("#")]
                for line in lines:
                    prof.learn(line, save=False)
                print(f"[Profile] new profile '{name}' seeded with {len(lines)} phrases")
            prof.save()
        self.profile = prof
        self.personalization = prof.ngrams
        self.session_history.clear()
        self.ai_cache = {}
        self._last_cands = ("", [])
        try:
            os.makedirs(PROFILES_DIR, exist_ok=True)
            with open(self._active_file(), "w", encoding="utf-8") as f:
                f.write(name)
        except Exception as e:
            print(f"[Profile] could not remember the active profile: {e}")
        print(f"[Profile] active profile: {name} ({len(prof.phrases)} phrases, {len(prof.words)} words)")
        return {"active": name, "profiles": self.list_profiles(), "phrases": len(prof.phrases)}

    def _detect_system_gpu(self):
        import shutil, subprocess
        import llama_cpp
        device_name = None
        if shutil.which("nvidia-smi"):
            try:
                out = subprocess.check_output(
                    ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                    encoding="utf-8"
                )
                device_name = out.strip().splitlines()[0]
            except Exception:
                pass
        return {
            "gpu_detected": bool(device_name),
            "device_name": device_name or "CPU",
            "llama_cuda": llama_cpp.llama_supports_gpu_offload()
        }

    def _load_llm_models(self, use_gpu: bool):
        self.use_gpu = use_gpu
        gpu_info = self._detect_system_gpu()
        gpu_layers = -1 if (use_gpu and gpu_info["llama_cuda"]) else 0
        target_device = gpu_info["device_name"] if (use_gpu and gpu_info["llama_cuda"]) else "CPU"
        print(f"[Backend] Loading models targeting {target_device} (use_gpu={use_gpu}, n_gpu_layers={gpu_layers})...")

        # logits_all=False: only the last token's scores are needed (see _top_next_tokens); logits_all=True kept a
        # score row for every prompt token and made each Space ~10x slower (research/tests_20261123)
        self.fast_llm = Llama(
            model_path=self.fast_gguf_path,
            n_ctx=512,
            n_gpu_layers=gpu_layers,
            logits_all=False,
            verbose=False,
            n_threads=8
        )
        self._fast_topk = self._check_fast_topk()
        if not self._fast_topk:                  # e.g. a newer llama-cpp-python: back to the old, slower way
            del self.fast_llm
            self.fast_llm = Llama(model_path=self.fast_gguf_path, n_ctx=512, n_gpu_layers=gpu_layers,
                                  logits_all=True, verbose=False, n_threads=8)
        self.heavy_llm = Llama(
            model_path=self.heavy_gguf_path,
            n_ctx=512,
            n_gpu_layers=gpu_layers,
            verbose=False,
            n_threads=8
        )
        print("[Backend] Models loaded successfully.")

    def set_gpu_mode(self, enable_gpu: bool):
        if hasattr(self, 'fast_llm') and self.use_gpu == enable_gpu:
            return self.get_gpu_info()
        print(f"[Backend] Changing GPU mode: enable_gpu={enable_gpu}")
        if hasattr(self, 'fast_llm'):
            del self.fast_llm
        if hasattr(self, 'heavy_llm'):
            del self.heavy_llm
        self._load_llm_models(use_gpu=enable_gpu)
        return self.get_gpu_info()

    def get_gpu_info(self):
        gpu_info = self._detect_system_gpu()
        if self.use_gpu and gpu_info["llama_cuda"]:
            active_mode = "GPU Accelerated (CUDA)"
        elif self.use_gpu and gpu_info["gpu_detected"]:
            active_mode = "GPU Enabled (CPU fallback)"
        else:
            active_mode = "CPU Mode"

        return {
            "gpu_available": gpu_info["gpu_detected"],
            "device_name": gpu_info["device_name"],
            "gpu_enabled": self.use_gpu,
            "llama_cuda": gpu_info["llama_cuda"],
            "active_mode": active_mode,
        }

    # ---------------------------------------------------------
    # INTERNAL: Build context-aware LLM prompt (Action 1)
    # ---------------------------------------------------------
    def _build_llm_prompt(self, current_sentence: str) -> str:
        """
        Builds the prompt for the 0.5B word prediction model, specifically instructing
        it to focus on daily life conversation words.
        """
        history_str = ""
        history = list(self.session_history)       # copy: a spoken sentence may be added at the same moment
        if history:
            history_str = "Context from earlier in this conversation:\n"
            for s in history:
                history_str += f"- {s}\n"
            history_str += "\n"

        prompt = (
            "You are an assistant predicting the next word for daily life conversations. "
            "Complete the sentence below with the single most natural and contextually relevant next word for a daily life scenario of a patient. "
            "Output only the word, nothing else.\n\n"
            f"{history_str}"
            f"Sentence: {current_sentence.rstrip()}"   # no trailing space: the model's words start with one
        )
        return prompt

    # ---------------------------------------------------------
    # INTERNAL: Get LLM scores (Actions 1, 5)
    # ---------------------------------------------------------
    def _top_next_tokens(self, prompt: str, n: int = 200) -> dict:
        """{token text: logprob} of the n most likely next tokens - the same dict as
        create_completion(prompt, max_tokens=1, logprobs=n, temperature=0)["choices"][0]["logprobs"]["top_logprobs"][0],
        but without logits_all=True: the prompt is evaluated (reusing the cached prefix like Llama.generate) and only
        the LAST token's scores are read. Same tokenization, the library's own logits_to_logprobs, same sort.
        Measured: 15/16 presses bit-identical, the cold first press differs only by CPU rounding (~0.2 %); median
        264 -> 27 ms per Space. Uses llama-cpp-python 0.3.28 internals - checked at load (_check_fast_topk)."""
        llm = self.fast_llm
        bos = llm.token_bos()
        toks = (([bos] if (llm._model.add_bos_token() and bos != -1) else [])
                + llm.tokenize(prompt.encode("utf-8"), add_bos=False, special=True))
        if not toks or len(toks) >= llm.n_ctx():
            raise ValueError(f"prompt of {len(toks)} tokens does not fit the context ({llm.n_ctx()})")
        keep = 0                                   # cached prefix (the last token is always evaluated again)
        for a, b in zip(llm.input_ids[:llm.n_tokens], toks[:-1]):
            if a != b:
                break
            keep += 1
        if keep > 0 and llm._ctx.kv_cache_seq_rm(-1, keep, -1):
            llm.n_tokens = keep
            llm.eval(toks[keep:])
        else:
            llm.reset()
            llm.eval(toks)
        logits = np.ctypeslib.as_array(llm._ctx.get_logits(), shape=(llm.n_vocab(),)).copy()
        logp = Llama.logits_to_logprobs(logits)
        cand = np.nonzero(logp >= np.partition(logp, -n)[-n])[0]          # the top n, ties included
        best_n = sorted(((float(logp[t]), int(t)) for t in cand), reverse=True)[:n]   # library's sort key
        top = {llm.detokenize([t]).decode("utf-8", errors="ignore"): v for v, t in best_n}
        best = best_n[0][1]                         # the greedy (temperature 0) token, as the library adds it
        top[llm.detokenize([best], prev_tokens=toks).decode("utf-8", errors="ignore")] = float(logp[best])
        return top

    def _check_fast_topk(self) -> bool:
        """Startup check of _top_next_tokens (it relies on llama-cpp-python internals); False -> old way."""
        try:
            top = self._top_next_tokens("Sentence: I want", 5)
            if len(top) >= 1 and all(isinstance(v, float) and v <= 0.0 for v in top.values()):
                print("[Backend] Fast next-word scoring active (logits_all off).")
                return True
            print(f"[Backend] Fast next-word scoring check gave an odd result {top!r}; using the old way.")
        except Exception as e:
            print(f"[Backend] Fast next-word scoring not available ({e!r}); using the old way (logits_all).")
        return False

    def _get_llm_scores(self, current_sentence: str) -> dict:
        """
        Always runs the LLM with a context-aware prompt and expanded logprobs.
        Returns a normalized {word: score} dict.
        """
        prompt = self._build_llm_prompt(current_sentence)

        try:
            if self._fast_topk:
                top_tokens = self._top_next_tokens(prompt, 200)
            else:
                output = self.fast_llm.create_completion(
                    prompt=prompt,
                    max_tokens=1,
                    logprobs=200,       # ACTION 5: was 50, now 200 for broader candidates
                    echo=False,
                    temperature=0,      # Greedy, deterministic — best single prediction
                )
                top_tokens = output["choices"][0]["logprobs"]["top_logprobs"][0]
        except Exception as e:
            print(f"[LLM] Error during completion: {e}")
            return {}

        ai_cache = {}                              # built aside, then swapped in at once (/api/type reads it)
        raw_scores = {}
        for token_str, logprob in top_tokens.items():
            # the prompt ends right after the last word, so a NEW word starts with a space; a piece without one
            # continues the last word ("can" -> "'t", "want" -> "ing") and is not a next word
            if not token_str.startswith((" ", "Ġ")):
                continue
            valid_word = self.clean_and_validate_token(token_str)
            if valid_word:
                prob = math.exp(logprob)
                if valid_word not in raw_scores or raw_scores[valid_word] < prob:
                    raw_scores[valid_word] = prob
                if valid_word not in ai_cache:
                    ai_cache[valid_word] = logprob
        self.ai_cache = ai_cache

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
        # only real words: the word list (vocab.json) or a word this profile has spoken (names etc.). Was "or any
        # piece of 4+ letters", which let word pieces such as "medicat" (first token of "medication") through.
        if self.trie.is_valid_word(clean) or clean in self.profile.words:
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
        if self._last_cands[0] != current_sentence.rstrip():     # the model's list (on_spacebar) is better: keep it
            self._last_cands = (current_sentence.rstrip(), [w for w, s in sorted_words])

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
            top_n=200
        )
        self._last_cands = (current_sentence.rstrip(), list(top_words))   # for completing the next word
        top_words = top_words[:10]

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

        # Phrase memory first: sentences this profile has spoken that start with the typed text
        matches = self.profile.phrases_for(current_text, n=3)
        seen = {UserProfile.key(m) for m in matches}
        try:
            history_str = ""
            history = list(self.session_history)   # copy: saving a spoken sentence no longer waits for this
            if history:
                history_str = "\n".join(f"- {s}" for s in history)
                history_str = f"Prior context:\n{history_str}\n\n"

            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are an AAC (Augmentative and Alternative Communication) keyboard assistant for patients with severe motor disabilities. "
                        "Your job is to generate simple, natural, clear everyday sentences that a patient would use to communicate daily needs, feelings, or requests to caregivers, family, or medical staff. "
                        "Output exactly 5 short, natural completions. Each sentence must start with the user's provided text. "
                        "Output only a numbered list from 1 to 5 with no extra conversational text."
                    )
                },
                {
                    "role": "user",
                    "content": "Provide 5 completions for this text:\ni want"
                },
                {
                    "role": "assistant",
                    "content": "1. I want water please.\n2. I want to rest now.\n3. I want to watch TV.\n4. I want to turn off the lights.\n5. I want to talk to my doctor."
                },
                {
                    "role": "user",
                    "content": "Provide 5 completions for this text:\ni feel"
                },
                {
                    "role": "assistant",
                    "content": "1. I feel tired today.\n2. I feel much better now.\n3. I feel pain in my back.\n4. I feel cold, please cover me.\n5. I feel hungry."
                },
                {
                    "role": "user",
                    "content": f"{history_str}Provide 5 completions for this text:\n{current_text}"
                }
            ]

            output = self.heavy_llm.create_chat_completion(
                messages=messages,
                max_tokens=150,
                temperature=0.2,
            )
            text = output["choices"][0]["message"]["content"].strip()

            lines = text.split('\n')
            typed_key = UserProfile.key(current_text)   # compared loosely: case, punctuation, extra spaces ignored
            got, dropped, n_phrases = 0, [], len(matches)
            for line in lines:
                match = re.match(r'^\d+\.\s*(.*)', line.strip())
                if match:
                    got += 1
                    full_sentence = match.group(1).strip()
                    full_sentence = re.sub(r'\s+([.,?!])', r'\1', full_sentence)
                    if full_sentence and full_sentence[-1] not in ['.', '?', '!']:
                        full_sentence += "."
                    if len(full_sentence) > 0:
                        full_sentence = full_sentence[0].upper() + full_sentence[1:]
                    if "_" in full_sentence or ".." in full_sentence or "()" in full_sentence.replace(" ", "") or "（）" in full_sentence:
                        dropped.append(full_sentence)
                        continue
                    k = UserProfile.key(full_sentence)
                    if len(k) > len(typed_key) and k.startswith(typed_key) and k not in seen:
                        seen.add(k)
                        matches.append(full_sentence)
                        if len(matches) >= count:
                            break
                    else:
                        dropped.append(full_sentence)
            # one line per request: shows why a list can stay empty (e.g. the model reworded the typed text)
            print(f"[Sentences] {current_text!r}: model gave {got}, kept {len(matches) - n_phrases}"
                  + (f"; dropped {dropped[:3]}" if dropped else ""), flush=True)
        except Exception as e:
            print(f"Error in LLM sentence completion: {e}", flush=True)

        return matches[:count]

    # ---------------------------------------------------------
    # PUBLIC: On Letter Typed — Prefix Prediction
    # ---------------------------------------------------------
    def on_letter_typed(self, prefix: str, context: str = None):
        """Prefix completion. With `context` (the whole typed text) words that fit the sentence come first.
        Order: sentence-fitting candidates, the profile's own words, the model's last candidates,
        then the word list ranked by everyday conversation (encyclopedia-only words last)."""
        prefix = prefix.lower().strip()
        if not prefix:
            return []
        ctx_matches = []
        if context is not None and context.split():
            # text before the last word; rsplit ignores trailing spaces, so they cannot cut into the previous word
            parts = context.rsplit(None, 1)
            before = parts[0].rstrip() if len(parts) > 1 else ""
            if before:
                if self._last_cands[0] == before:
                    cands = self._last_cands[1]
                else:                                   # not from the last space: n-grams only (fast)
                    p, t, _ = self._get_ngram_scores_with_backoff(before + " ")
                    sc = {w: max(p.get(w, 0.0), t.get(w, 0.0)) for w in set(p) | set(t)}
                    cands = sorted(sc, key=lambda w: -sc[w])
                ctx_matches = [w for w in cands if w.startswith(prefix)][:self.CTX_SLOTS]
        # copies taken in one step: a sentence being learned / a new model list cannot break these loops
        words = list(self.profile.words.items())
        own = [w for w, c in sorted(words, key=lambda wc: -wc[1]) if w.startswith(prefix)]
        ai = self.ai_cache
        ai_matches = sorted((w for w in ai if w.startswith(prefix)), key=lambda w: -ai[w])
        final, seen = [], set()
        for src, words in (("ai", ctx_matches), ("trie", own), ("ai", ai_matches), ("trie", self._everyday(prefix))):
            for w in words:
                if w not in seen:
                    seen.add(w)
                    final.append({"word": w, "source": src})
        return final[:10]

    CTX_SLOTS = 5      # at most this many sentence-fitting words, so plain completions keep some places

    def _everyday(self, prefix: str, n: int = 10) -> list:
        """vocab.json completions, words used in everyday conversation first (by conversation frequency),
        then the rest in vocab order; single letters other than 'a'/'i' dropped."""
        cand = [w for w in self.trie.get_words_with_prefix(prefix, top_n=200) if len(w) > 1 or w in ("a", "i")]
        order = {w: i for i, w in enumerate(cand)}
        cand.sort(key=lambda w: (self.conv_freq.get(w, 0) == 0, -self.conv_freq.get(w, 0), order[w]))
        return cand[:n]

    # ---------------------------------------------------------
    # PUBLIC: Learn User Typing (Action 6 — Fixed)
    # ---------------------------------------------------------
    def learn_user_typing(self, full_sentence: str):
        """Learns a spoken sentence into the ACTIVE profile only (n-grams, words, phrase memory) and saves it."""
        self.profile.learn(full_sentence)


if __name__ == "__main__":
    print("HybridKeyboardBackend module loaded successfully.")
