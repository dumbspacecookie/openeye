# eval dataset: procedure verification

100 examples for evaluating LLM performance on procedure verification tasks.

## methodology

each example presents a scene description and asks the model to classify the step as `pass`, `fail`, or `uncertain`. the model responds with a single word.

scoring:
- exact match: 1.0 point
- wrong binary (pass/fail): 0.0 points
- uncertain vs pass/fail (or vice versa): 0.5 points

## distribution

| difficulty | count | description |
|---|---|---|
| easy | 30 | clear visual evidence, unambiguous ground truth |
| medium | 50 | partially obscured or incomplete evidence |
| hard | 20 | genuinely ambiguous — reasonable models should output uncertain |

| procedure | count |
|---|---|
| hand-hygiene | 30 |
| equipment-check | 30 |
| field-inspection | 25 |
| trocar-placement | 15 |

## reproducing

```bash
python eval/scripts/generate_eval_data.py
# random.seed(42) ensures deterministic output
```

---

*— dumbspacecookie*
