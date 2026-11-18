"""Translate regional-language news into English with IndicTrans2 (AI4Bharat, MIT licence), offline on CPU.

Model: ai4bharat/indictrans2-indic-en-dist-200M, chosen after comparing six open-source models on 12
languages (best translation quality and disease names kept in 90% of real health headlines). Only the
headline and the sentences that matter are translated (select_sentences), which keeps a run fast.

The model is downloaded once into models/ (access to the model must be granted on Hugging Face first,
and `hf auth login` run once):

    python -m outbreak_monitor.translate --download
"""

import argparse
import logging
import os
import threading
import unicodedata
from pathlib import Path

import regex as re

log = logging.getLogger(__name__)

MODEL_REPO = "ai4bharat/indictrans2-indic-en-dist-200M"
MODEL_DIR = Path(os.environ.get("OUTBREAK_MT_MODEL_DIR",
                                Path(__file__).resolve().parent.parent / "models" / "indictrans2-indic-en-dist-200M"))
# ISO 639-1 code -> IndicTrans2 (FLORES-200) code
MODEL_LANGUAGES = {"hi": "hin_Deva", "bn": "ben_Beng", "mr": "mar_Deva", "te": "tel_Telu", "ta": "tam_Taml",
                   "gu": "guj_Gujr", "ur": "urd_Arab", "kn": "kan_Knda", "or": "ory_Orya", "ml": "mal_Mlym",
                   "pa": "pan_Guru", "as": "asm_Beng"}
BATCH_SIZE = 8
NUM_BEAMS = 2           # the setting used in the model comparison
MAX_TOKENS = 256
MAX_SENTENCE_CHARS = 600
LEAD_SENTENCES = 2      # always translated: the opening usually says where and when
MAX_SENTENCES = 12      # per article, besides the headline

# Sentence ends: . ? ! and the Devanagari danda (।, ॥), the Urdu full stop (۔); also line breaks.
_SENTENCE_END = re.compile(r"(?<=[.?!।॥۔])\s+|\n+")
# Letters of the Latin script vs. any other script (to recognise English text on a regional site)
_LATIN = re.compile(r"\p{Latin}")
_LETTER = re.compile(r"\p{L}")
# A sign the model's pre-processing could not map (e.g. the nukta of Marathi "ऱ") comes back as "\ u093C";
# rarely, a placeholder for a code like "PJ.2.1" comes back mangled ("< ID1\ 1") and cannot be restored.
_LEFTOVER_CODE = re.compile(r"\s*\\\s?u[0-9a-fA-F]{4}|\s*[<\[]\s*id\s*\d+\s*(?:[>\]]|\\\s*\d+)?", re.IGNORECASE)


class TranslatorUnavailable(Exception):
    """The translation model is not installed or could not be loaded."""


def split_sentences(text: str | None) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text or "") if s and s.strip()]


def mostly_latin(text: str | None) -> bool:
    """True if most letters are Latin script (an English article on a regional-language site)."""
    letters = _LETTER.findall(text or "")
    return bool(letters) and len(_LATIN.findall(text or "")) / len(letters) > 0.5


def select_sentences(sentences: list[str], wanted) -> list[str]:
    """The sentences to translate, in article order: the first LEAD_SENTENCES and every sentence for which
    wanted(sentence) is true (it names a disease or a screening word), at most MAX_SENTENCES."""
    picked = [s for i, s in enumerate(sentences) if i < LEAD_SENTENCES or wanted(s)]
    return [s[:MAX_SENTENCE_CHARS] for s in picked[:MAX_SENTENCES]]


class Translator:
    """IndicTrans2 Indic -> English. Loaded on first use; one instance is shared by the whole process and
    translation calls are serialised (the model uses all CPU cores by itself)."""

    def __init__(self, model_dir: Path = MODEL_DIR):
        self.model_dir = Path(model_dir)
        self._lock = threading.Lock()
        self._model = self._tokenizer = self._processor = None

    def available(self) -> bool:
        return (self.model_dir / "config.json").exists()

    def _load(self) -> None:
        if self._model is not None:
            return
        if not self.available():
            raise TranslatorUnavailable(f"translation model not found in {self.model_dir} "
                                        "(python -m outbreak_monitor.translate --download)")
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        from .indic_processor import IndicProcessor
        log.info("Loading translation model %s", self.model_dir.name)
        # trust_remote_code: the model's own code (modeling_indictrans.py) ships with the downloaded model
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_dir, trust_remote_code=True)
        self._model = AutoModelForSeq2SeqLM.from_pretrained(self.model_dir, trust_remote_code=True).eval()
        self._processor = IndicProcessor(inference=True)
        self._torch = torch

    def translate(self, language: str, texts: list[str]) -> list[str]:
        """English translations of texts written in `language` (ISO 639-1 code), in the same order."""
        if language not in MODEL_LANGUAGES:
            raise ValueError(f"no translation for language {language!r}")
        # NFC: some sites write letters with a nukta as one precomposed character (ਸ਼, য়); the model's
        # pre-processing mishandles those and leaves codes like "\ u0A3C" in the translation.
        texts = [" ".join(unicodedata.normalize("NFC", t).split()) for t in texts]
        out = [""] * len(texts)
        todo = [i for i, t in enumerate(texts) if t]
        if not todo:
            return out
        with self._lock:
            self._load()
            src = MODEL_LANGUAGES[language]
            for start in range(0, len(todo), BATCH_SIZE):
                idx = todo[start:start + BATCH_SIZE]
                batch = self._processor.preprocess_batch([texts[i] for i in idx], src_lang=src, tgt_lang="eng_Latn")
                enc = self._tokenizer(batch, return_tensors="pt", padding="longest", truncation=True,
                                      max_length=MAX_TOKENS)
                with self._torch.inference_mode():
                    gen = self._model.generate(**enc, num_beams=NUM_BEAMS, max_length=MAX_TOKENS, use_cache=True)
                decoded = self._tokenizer.batch_decode(gen, skip_special_tokens=True, clean_up_tokenization_spaces=True)
                for i, text in zip(idx, self._processor.postprocess_batch(decoded, lang="eng_Latn")):
                    out[i] = _LEFTOVER_CODE.sub("", text).strip()
        return out


_shared: Translator | None = None


def get_translator() -> Translator:
    global _shared
    if _shared is None:
        _shared = Translator()
    return _shared


def download(model_dir: Path = MODEL_DIR) -> None:
    """Download the model files (the Hugging Face account must have been granted access to the model)."""
    from huggingface_hub import snapshot_download
    snapshot_download(MODEL_REPO, local_dir=model_dir,
                      allow_patterns=["*.json", "*.py", "*.safetensors", "model.SRC", "model.TGT", "dict*", "LICENSE"])
    print(f"Model saved in {model_dir}")


def main():
    parser = argparse.ArgumentParser(description="Translation model for regional-language news.")
    parser.add_argument("--download", action="store_true", help=f"download {MODEL_REPO} into {MODEL_DIR}")
    parser.add_argument("--language", help="ISO code of a sentence to translate as a test, e.g. hi")
    parser.add_argument("text", nargs="?", help="sentence to translate as a test")
    args = parser.parse_args()
    if args.download:
        download()
    if args.text:
        print(get_translator().translate(args.language or "hi", [args.text])[0])


if __name__ == "__main__":
    main()
