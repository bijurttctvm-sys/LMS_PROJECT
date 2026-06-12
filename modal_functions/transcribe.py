"""
Modal GPU functions for the LMS pipeline.

Deploy:  modal deploy modal_functions/transcribe.py
"""

import modal

app = modal.App("lms-transcription")

model_cache = modal.Volume.from_name("lms-model-cache", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git")
    .pip_install(
        "torch==2.2.2",
        "transformers==4.40.2",
        "sentencepiece==0.2.0",
        "sacremoses==0.1.1",
        "numpy==1.26.4",
        "accelerate==0.30.1",
    )
    .run_commands(
        "pip install --no-deps git+https://github.com/VarunGumma/IndicTransToolkit.git"
    )
)


# ---------------------------------------------------------------------------
# IndicTranslator — ai4bharat/indictrans2-en-indic-1B
# ---------------------------------------------------------------------------

@app.cls(
    gpu="T4",
    image=image,
    scaledown_window=600,
    volumes={"/cache": model_cache},
)
class IndicTranslator:

    @modal.enter()
    def load_model(self):
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        model_id = "ai4bharat/indictrans2-en-indic-1B"
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_id,
            trust_remote_code=True,
            cache_dir="/cache/indictrans2",
        )
        self.model = AutoModelForSeq2SeqLM.from_pretrained(
            model_id,
            trust_remote_code=True,
            cache_dir="/cache/indictrans2",
        ).to(torch.device("cuda"))
        self.model.eval()

        try:
            from IndicTransToolkit.processor import IndicProcessor
            self.ip = IndicProcessor(inference=True)
        except Exception:
            self.ip = None

    @modal.method()
    def translate(
        self,
        text: str,
        source_lang: str = "eng_Latn",
        target_lang: str = "mal_Mlym",
    ) -> str:
        import torch

        sentences = [text]

        if self.ip:
            batch = self.ip.preprocess_batch(
                sentences, src_lang=source_lang, tgt_lang=target_lang
            )
        else:
            batch = sentences

        inputs = self.tokenizer(
            batch,
            truncation=True,
            padding="longest",
            return_tensors="pt",
            max_length=512,
        ).to("cuda")

        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                num_beams=4,
                num_return_sequences=1,
                max_length=512,
            )

        decoded = self.tokenizer.batch_decode(
            outputs,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=True,
        )

        if self.ip:
            result = self.ip.postprocess_batch(decoded, lang=target_lang)
            return result[0] if result else ""

        return decoded[0] if decoded else ""


# ---------------------------------------------------------------------------
# EmbeddingGenerator — intfloat/multilingual-e5-large (Phase 5)
# ---------------------------------------------------------------------------

@app.cls(
    gpu="T4",
    image=image,
    scaledown_window=600,
    volumes={"/cache": model_cache},
)
class EmbeddingGenerator:

    @modal.enter()
    def load_model(self):
        import torch
        from transformers import AutoModel, AutoTokenizer

        model_id = "intfloat/multilingual-e5-large"
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_id, cache_dir="/cache/e5"
        )
        self.model = AutoModel.from_pretrained(
            model_id, cache_dir="/cache/e5"
        ).to(torch.device("cuda"))
        self.model.eval()

    @modal.method()
    def generate(self, texts: list) -> list:
        import torch
        import torch.nn.functional as F

        inputs = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        ).to("cuda")

        with torch.no_grad():
            outputs = self.model(**inputs)
            # Mean pooling
            token_embs = outputs.last_hidden_state
            attn_mask = inputs["attention_mask"].unsqueeze(-1).float()
            mean_emb = (token_embs * attn_mask).sum(1) / attn_mask.sum(1)
            # L2 normalise
            normalised = F.normalize(mean_emb, p=2, dim=1)

        return normalised.cpu().tolist()


# ---------------------------------------------------------------------------
# Local smoke-test entry point
# ---------------------------------------------------------------------------

@app.local_entrypoint()
def test_deploy():
    print("IndicTranslator and EmbeddingGenerator are defined — deploy with:")
    print("  modal deploy modal_functions/transcribe.py")
