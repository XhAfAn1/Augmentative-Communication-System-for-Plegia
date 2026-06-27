import collections
import re
import json
import os
from llama_cpp import Llama

# ---------------------------------------------------------
# 1. FIXED & NORMALIZED FREQUENCY TRIE
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
        """Inserts a word and accurately updates its leaf node frequency."""
        word = word.lower().strip()
        if not word:
            return
            
        node = self.root
        for char in word:
            if char not in node.children:
                node.children[char] = TrieNode()
            node = node.children[char]
        
        node.is_end_of_word = True
        node.frequency = max(node.frequency, frequency)
        self._all_words.append((word, frequency))

    def update_frequency(self, word: str, increment: int = 100):
        """Incrementally updates the frequency of a word over time."""
        word = word.lower().strip()
        if not word:
            return
            
        node = self.root
        for char in word:
            if char not in node.children:
                node.children[char] = TrieNode()
            node = node.children[char]
            
        node.is_end_of_word = True
        node.frequency += increment

    def build_cache(self):
        """Caches the top most frequent words in the entire dictionary."""
        self._all_words.sort(key=lambda x: x[1], reverse=True)
        seen = set()
        self.top_global_words = []
        for w, f in self._all_words:
            if w not in seen:
                seen.add(w)
                self.top_global_words.append(w)
                if len(self.top_global_words) >= 10:
                    break
        self._all_words = [] # free memory

    def is_valid_word(self, word: str) -> bool:
        """Strict validation check to see if a string forms a complete known word."""
        word = word.lower().strip()
        node = self.root
        for char in word:
            if char not in node.children:
                return False
            node = node.children[char]
        return node.is_end_of_word

    def get_words_with_prefix(self, prefix: str, top_n: int = 10):
        """Finds all words matching a prefix, sorted descending by frequency."""
        prefix = prefix.lower().strip()
        if not prefix:
            return self.top_global_words[:top_n]
            
        node = self.root
        for char in prefix:
            if char not in node.children:
                return [] 
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
# 2. INTELLECTUAL HYBRID ORCHESTRATOR
# ---------------------------------------------------------
class HybridKeyboardBackend:
    def __init__(self, gguf_path: str, vocab_path: str):
        self.trie = FrequencyTrie()
        
        if os.path.exists(vocab_path):
            with open(vocab_path, 'r') as f:
                base_vocab = json.load(f)
            for word, freq in base_vocab.items():
                self.trie.insert(word, freq)
            self.trie.build_cache()
        else:
            print(f"Warning: {vocab_path} not found. Trie will be empty.")
            
        # Initialize the local LLM model
        self.llm = Llama(model_path=gguf_path, n_ctx=512, logits_all=True, verbose=False)
        self.ai_cache = {}

    def clean_and_validate_token(self, token_str: str):
        """
        Cleans tokenizer markers and strictly ensures that sub-word 
        fragments (like 'ing' or 'tion') are not pushed to the UI.
        """
        # Strip common BPE character/space markers used by Llama/Qwen engines
        clean = token_str.replace("Ġ", "").replace(" ", "").strip().lower()
        
        # Strip trailing or leading common punctuation attached to tokens
        clean = re.sub(r"^[^\w]+|[^\w]+$", "", clean)
        
        # Ensure it contains text and matches valid grammar rules (e.g., "don't")
        if not re.match(r"^[a-z]+(?:'[a-z]+)?$", clean):
            return None
            
        # Strict validation check. The token is approved if it exists 
        # inside our system vocabulary OR if it's a longer alphabetic string (e.g., a proper noun).
        if self.trie.is_valid_word(clean) or len(clean) >= 4:
            return clean
            
        return None

    def on_spacebar(self, current_sentence: str):
        """Triggered at word boundaries to fetch next-word context scores."""
        # Use simple fallback if sentence is too short or empty
        if not current_sentence.strip():
            return []
            
        output = self.llm.create_completion(
            prompt=current_sentence,
            max_tokens=1,
            logprobs=150, # Fetch a wider candidate window to allow for filter dropouts
            echo=False
        )
        
        top_tokens = output["choices"][0]["logprobs"]["top_logprobs"][0]
        self.ai_cache = {}
        
        for token_str, logprob in top_tokens.items():
            valid_word = self.clean_and_validate_token(token_str)
            if valid_word:
                # Store the probability weight for subsequent prefix matching
                if valid_word not in self.ai_cache:
                    self.ai_cache[valid_word] = logprob
                    
        # Sort candidates cleanly by their contextual likelihood
        sorted_ai = sorted(self.ai_cache.keys(), key=lambda w: self.ai_cache[w], reverse=True)
        return [{"word": w, "source": "ai"} for w in sorted_ai[:8]]

    def on_letter_typed(self, prefix: str):
        """Triggered on instant keystrokes to return low-latency predictions."""
        prefix = prefix.lower().strip()
        
        # 1. Fetch frequency-ranked completions from the Trie database
        trie_matches = self.trie.get_words_with_prefix(prefix, top_n=10)
        
        # 2. Filter local AI cache options against what the user is typing
        ai_matches = [w for w in self.ai_cache if w.startswith(prefix)]
        ai_matches.sort(key=lambda w: self.ai_cache[w], reverse=True)
        
        # 3. Interleave matching profiles securely, maintaining unique orders
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
                
        return final_suggestions[:8]

    def learn_user_typing(self, finished_word: str):
        """Dynamically tracks custom user behavior and pushes frequencies up incrementally."""
        word = finished_word.lower().strip()
        if word.isalpha():
            # Dynamically incrementally scales up frequency scores on the fly
            self.trie.update_frequency(word, increment=100)

    def predict_sentences(self, current_sentence: str):
        """Generates 3 full sentence continuations using the LLM"""
        sentences = set()
        base = current_sentence if current_sentence.strip() else "I "
        
        for _ in range(3):
            output = self.llm.create_completion(
                prompt=base,
                max_tokens=15,
                temperature=0.8,
                stop=["\n", ".", "?", "!"],
                echo=False
            )
            # Clean up the output string
            generated = output["choices"][0]["text"].strip().replace("Ġ", " ")
            res = base + generated
            
            # Avoid completely empty outputs
            if len(res.strip()) > 0:
                sentences.add(res.strip())
                
        final_list = list(sentences)
        # Pad with placeholders if the model generated duplicates
        while len(final_list) < 3:
            final_list.append(base.strip() + " ...")
            
        return [{"sentence": s} for s in final_list[:3]]
