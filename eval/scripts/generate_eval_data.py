"""
generate_eval_data.py — generates the 100-example procedure verification eval set.

Deterministic: always call with random.seed(42) so output is reproducible.
Output: eval-data/procedure_verification_eval.json

Run:
    python eval/scripts/generate_eval_data.py [--output eval/eval-data/procedure_verification_eval.json]
"""
import argparse
import json
import os
import random

# ── Templates ────────────────────────────────────────────────────────────────

PROCEDURES = {
    "hand-hygiene": {
        "steps": [
            ("hh-wash", "hand washing"),
            ("hh-soap", "soap application"),
            ("hh-rinse", "rinse"),
            ("hh-dry", "dry with sterile towel"),
            ("hh-glove", "sterile glove donning"),
        ],
        "objects": ["hands", "soap", "water", "foam", "towel", "gloves", "sink", "dispenser"],
    },
    "equipment-check": {
        "steps": [
            ("ec-power", "power system check"),
            ("ec-guards", "safety guard verification"),
            ("ec-fluids", "fluid level check"),
            ("ec-calibration", "calibration verification"),
        ],
        "objects": ["control-panel", "indicator-light", "guard", "interlock", "sight-glass", "coolant", "calibration-sticker"],
    },
    "field-inspection": {
        "steps": [
            ("fi-identify", "energy source identification"),
            ("fi-notify", "notification"),
            ("fi-lock", "lockout application"),
            ("fi-verify", "energy verification"),
            ("fi-ppe", "PPE compliance check"),
        ],
        "objects": ["lock", "tag", "breaker", "gauge", "hard-hat", "gloves", "safety-glasses"],
    },
    "trocar-placement": {
        "steps": [
            ("tp-mark", "incision site marking"),
            ("tp-insufflate", "insufflation"),
            ("tp-insert", "trocar insertion"),
        ],
        "objects": ["trocar", "insufflator", "marking-pen", "abdomen", "co2-line", "pressure-gauge"],
    },
}

DIFFICULTY_DISTRIBUTION = {"easy": 30, "medium": 50, "hard": 20}
PROCEDURE_DISTRIBUTION = {"hand-hygiene": 30, "equipment-check": 30, "field-inspection": 25, "trocar-placement": 15}

SCENE_TEMPLATES = {
    "easy": {
        "pass": [
            "{obj1} clearly visible, {step_name} completed correctly, both hands in frame, good lighting",
            "operator performing {step_name} with {obj1} and {obj2}, all criteria met, clear view",
            "{step_name} verified — {obj1} in correct position, {obj2} present, no obstructions",
        ],
        "fail": [
            "{step_name} not performed — {obj1} absent, {obj2} not in expected position",
            "clear view shows {step_name} incomplete: {obj1} missing, operator hands not visible",
            "{obj1} present but {step_name} criteria not met — wrong technique clearly visible",
        ],
    },
    "medium": {
        "pass": [
            "{obj1} partially visible, {step_name} appears complete, slight occlusion from {obj2}",
            "operator performing {step_name}, {obj1} in frame but {obj2} partially behind equipment",
        ],
        "fail": [
            "{step_name} attempted but {obj1} shows incorrect positioning, {obj2} unclear",
            "{obj1} visible at edge of frame, {step_name} criteria partially unmet",
        ],
        "uncertain": [
            "{obj1} visible but {step_name} status unclear due to {obj2} occlusion",
            "glare on {obj1} obscures {step_name} result, {obj2} partially visible",
        ],
    },
    "hard": {
        "uncertain": [
            "{obj1} and {obj2} both at frame edge, {step_name} cannot be confirmed from this angle",
            "significant motion blur during {step_name}, {obj1} visible but result ambiguous",
            "camera angle prevents assessment of {step_name}, only {obj1} partially visible",
            "{step_name} may be complete but {obj2} occluded by personnel, {obj1} position unclear",
        ],
    },
}


def _generate_example(idx: int, procedure: str, difficulty: str, rng: random.Random) -> dict:
    proc_data = PROCEDURES[procedure]
    step_id, step_name = rng.choice(proc_data["steps"])
    objects = proc_data["objects"]

    # Determine ground truth based on difficulty
    if difficulty == "easy":
        ground_truth = rng.choice(["pass", "fail"])
        templates = SCENE_TEMPLATES["easy"][ground_truth]
    elif difficulty == "medium":
        ground_truth = rng.choice(["pass", "fail", "uncertain"])
        if ground_truth in SCENE_TEMPLATES["medium"]:
            templates = SCENE_TEMPLATES["medium"][ground_truth]
        else:
            templates = SCENE_TEMPLATES["medium"]["pass"]
    else:  # hard
        ground_truth = "uncertain"
        templates = SCENE_TEMPLATES["hard"]["uncertain"]

    template = rng.choice(templates)
    obj1, obj2 = rng.sample(objects, min(2, len(objects)))
    scene = template.format(obj1=obj1, obj2=obj2, step_name=step_name)
    detected = rng.sample(objects, rng.randint(2, min(4, len(objects))))

    return {
        "id": f"eval-{idx:03d}",
        "procedure": procedure,
        "step_id": step_id,
        "step_name": step_name,
        "scene_description": scene,
        "objects_detected": detected,
        "ground_truth": ground_truth,
        "difficulty": difficulty,
    }


def generate(seed: int = 42) -> list:
    rng = random.Random(seed)
    examples = []
    idx = 1

    # Build pool: procedure x difficulty
    pool = []
    for proc, count in PROCEDURE_DISTRIBUTION.items():
        pool.extend([proc] * count)
    rng.shuffle(pool)

    # Assign difficulties proportionally
    diff_pool = []
    for diff, count in DIFFICULTY_DISTRIBUTION.items():
        diff_pool.extend([diff] * count)
    rng.shuffle(diff_pool)

    for proc, diff in zip(pool, diff_pool):
        examples.append(_generate_example(idx, proc, diff, rng))
        idx += 1

    return examples


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", default=os.path.join(os.path.dirname(__file__), "..", "eval-data", "procedure_verification_eval.json"))
    args = p.parse_args()

    data = generate(seed=42)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(data, f, indent=2)

    print(f"generated {len(data)} examples -> {args.output}")


if __name__ == "__main__":
    main()
