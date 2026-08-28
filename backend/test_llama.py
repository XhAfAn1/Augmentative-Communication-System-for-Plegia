from llama_cpp import Llama

llm = Llama(model_path="qwen2.5-0.5b-instruct-q8_0.gguf", n_ctx=512, n_gpu_layers=-1, logits_all=True, verbose=True)

output = llm.create_completion(
    prompt="I want to go to the ",
    max_tokens=1,
    logprobs=150,
    echo=False
)

top_tokens = output["choices"][0]["logprobs"]["top_logprobs"][0]
with open("tokens.txt", "w", encoding="utf-8") as f:
    for token_str, logprob in top_tokens.items():
        f.write(f"{repr(token_str)} : {float(logprob)}\n")
