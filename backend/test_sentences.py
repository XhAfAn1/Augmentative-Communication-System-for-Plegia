from llama_cpp import Llama
import time

llm = Llama(model_path="qwen2.5-0.5b-instruct-q8_0.gguf", n_ctx=512, verbose=False)

def predict_sentences(text):
    start = time.time()
    sentences = set()
    base = text if text else "I "
    for _ in range(3):
        output = llm.create_completion(
            prompt=base,
            max_tokens=12,
            temperature=0.8,
            stop=["\n", ".", "?", "!"],
            echo=False
        )
        res = base + output["choices"][0]["text"].strip()
        sentences.add(res.strip())
    print(f"Time taken: {time.time() - start:.2f}s")
    return list(sentences)

print("Empty:")
print(predict_sentences(""))
print("\nPartial 'I am feeling ':")
print(predict_sentences("I am feeling "))
print("\nPartial 'Can you ':")
print(predict_sentences("Can you "))
