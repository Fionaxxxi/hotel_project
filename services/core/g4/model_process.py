"""Offline numeric inference only. No database, web, credentials or business tools."""

import json
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer


def main():
    directory = Path(__file__).resolve().parents[3] / ".runtime/models/multilingual-minilm"
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(directory / "onnx/model_quint8_avx2.onnx"), options, providers=["CPUExecutionProvider"]
    )
    tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
    tokenizer.enable_truncation(max_length=128)
    tokenizer.enable_padding()
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        start = time.monotonic()
        payload = json.loads(line)
        texts = payload["texts"]
        if len(texts) > 11 or any(not isinstance(t, str) or len(t) > 2000 for t in texts):
            raise ValueError("MODEL_INPUT_LIMIT")
        encoded = tokenizer.encode_batch(texts)
        arrays = {
            "input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
            "attention_mask": np.array([e.attention_mask for e in encoded], dtype=np.int64),
            "token_type_ids": np.array([e.type_ids for e in encoded], dtype=np.int64),
        }
        output = session.run(None, {x.name: arrays[x.name] for x in session.get_inputs()})[0]
        mask = arrays["attention_mask"][:, :, None]
        embedding = (output * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
        embedding /= np.maximum(np.linalg.norm(embedding, axis=1, keepdims=True), 1e-9)
        scores = (embedding[1:] @ embedding[0]).tolist()
        print(json.dumps({"scores": scores, "seconds": time.monotonic() - start}), flush=True)


if __name__ == "__main__":
    main()
