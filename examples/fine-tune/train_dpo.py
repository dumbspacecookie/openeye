"""
DPO fine-tune Llama-3.2-3B on OpenEye-collected procedure-verification
trajectories using TRL's DPOTrainer.

This is intentionally small enough to run on a single T4 or A10. For
larger models, switch to QLoRA or move to A100/H100.

Run on Colab (recommended):
    !pip install transformers trl peft accelerate datasets bitsandbytes
    !python train_dpo.py --data trajectories_dpo.jsonl --output ./openeye-llama-3.2-3b-dpo

Run locally with GPU:
    pip install -r requirements.txt
    python train_dpo.py --data trajectories_dpo.jsonl

Run without GPU (CPU-only smoke test, will be slow):
    python train_dpo.py --data trajectories_dpo.jsonl --dry-run

Expected: 100 DPO pairs, 3 epochs, batch size 4 trains in:
    - T4 (16GB):  ~25 min
    - A10 (24GB): ~12 min
    - A100 (40GB): ~5 min
"""

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [train] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def load_pairs(path: str):
    pairs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            pairs.append(json.loads(line))
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="trajectories_dpo.jsonl",
                    help="JSONL produced by fetch_trajectories.py")
    ap.add_argument("--base-model", default="meta-llama/Llama-3.2-3B-Instruct")
    ap.add_argument("--output", default="./openeye-llama-3.2-3b-dpo")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--lr", type=float, default=5e-6)
    ap.add_argument("--max-length", type=int, default=1024)
    ap.add_argument("--beta", type=float, default=0.1,
                    help="DPO temperature — higher = more aggressive preference learning")
    ap.add_argument("--use-qlora", action="store_true",
                    help="Use 4-bit QLoRA — needed if VRAM < 24GB on a 3B model")
    ap.add_argument("--dry-run", action="store_true",
                    help="Parse data and print config, skip training. CPU-safe.")
    args = ap.parse_args()

    pairs = load_pairs(args.data)
    if not pairs:
        logger.error("No DPO pairs in %s. Run fetch_trajectories.py first.", args.data)
        sys.exit(1)

    logger.info("Loaded %d DPO pairs", len(pairs))
    procedures = set(p["procedure_tag"] for p in pairs)
    logger.info("Procedures covered: %s", sorted(procedures))

    if args.dry_run:
        logger.info("Dry run — config below, no training.")
        logger.info("  base_model:  %s", args.base_model)
        logger.info("  output:      %s", args.output)
        logger.info("  epochs:      %d", args.epochs)
        logger.info("  batch_size:  %d", args.batch_size)
        logger.info("  lr:          %g", args.lr)
        logger.info("  beta:        %g", args.beta)
        logger.info("  qlora:       %s", args.use_qlora)
        logger.info("  pairs:       %d", len(pairs))
        sample = pairs[0]
        logger.info("Sample pair (procedure=%s, chosen=%.2f, rejected=%.2f):",
                    sample["procedure_tag"], sample["chosen_reward"],
                    sample["rejected_reward"])
        logger.info("  prompt:   %s", sample["prompt"][:120])
        logger.info("  chosen:   %s", sample["chosen"][:120])
        logger.info("  rejected: %s", sample["rejected"][:120])
        return

    # Imports deferred so --dry-run works without the ML stack installed.
    from datasets import Dataset
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import DPOConfig, DPOTrainer
    import torch

    logger.info("Loading tokenizer + model: %s", args.base_model)
    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model_kwargs = {"torch_dtype": torch.bfloat16}
    if args.use_qlora:
        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
        )

    model = AutoModelForCausalLM.from_pretrained(args.base_model, **model_kwargs)
    # Reference model — required by DPO. For QLoRA this can be the same
    # model with adapters disabled; here we keep it simple.
    ref_model = AutoModelForCausalLM.from_pretrained(args.base_model, **model_kwargs)

    dataset = Dataset.from_list(pairs)
    logger.info("Dataset size: %d", len(dataset))

    config = DPOConfig(
        output_dir=args.output,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=1,
        learning_rate=args.lr,
        max_length=args.max_length,
        max_prompt_length=args.max_length // 2,
        beta=args.beta,
        logging_steps=10,
        save_strategy="epoch",
        report_to="none",  # set to "wandb" if you've configured it
        remove_unused_columns=False,
    )

    trainer = DPOTrainer(
        model=model,
        ref_model=ref_model,
        args=config,
        train_dataset=dataset,
        tokenizer=tokenizer,
    )

    logger.info("Starting DPO training...")
    trainer.train()
    logger.info("Saving final model to %s", args.output)
    trainer.save_model(args.output)
    tokenizer.save_pretrained(args.output)
    logger.info("Done. Run benchmark.py to compare vs. base.")


if __name__ == "__main__":
    main()
